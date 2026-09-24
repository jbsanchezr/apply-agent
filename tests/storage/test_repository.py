"""Idempotent recording and application matching."""

from typing import Any

import pytest
from sqlalchemy import Engine

from apply_agent.domain import ApplicationStatus, MessageAssessment
from apply_agent.storage import make_session_factory
from apply_agent.storage.repository import Repository
from tests.agent.helpers import assessment, message


@pytest.fixture
def repo(engine: Engine) -> Repository:
    return Repository(make_session_factory(engine))


def _assess(category: str = "other", **kwargs: Any) -> MessageAssessment:
    return MessageAssessment.model_validate(assessment(category, **kwargs))


def test_recording_the_same_message_twice_is_a_no_op(repo: Repository) -> None:
    first = repo.record(message("m1"), _assess(category="interview_invitation"))
    second = repo.record(message("m1"), _assess(category="rejection"))

    assert first.created_application
    assert not first.duplicate
    assert second.duplicate
    assert second.application_id == first.application_id
    [app] = repo.list_applications()
    assert app.status is ApplicationStatus.INTERVIEWING, "a replay must not change history"


def test_new_thread_with_same_company_and_role_joins_the_application(repo: Repository) -> None:
    repo.record(message("m1", "t1"), _assess(company="Kestrel Robotics GmbH"))
    repo.record(message("m2", "t2", days=1), _assess("rejection", company="kestrel robotics"))

    [app] = repo.list_applications()
    assert app.thread_ids == {"t1", "t2"}
    assert app.status is ApplicationStatus.REJECTED


def test_thread_link_beats_what_the_model_says_about_the_company(repo: Repository) -> None:
    repo.record(message("m1", "t1"), _assess(company="Brightloom"))
    repo.record(message("m2", "t1", days=1), _assess("interview_invitation", company="CalBooker"))
    assert len(repo.list_applications()) == 1


def test_role_less_reply_joins_the_only_application_at_that_company(repo: Repository) -> None:
    repo.record(message("m1", "t1"), _assess(role="Data Engineer"))
    repo.record(message("m2", "t2", days=1), _assess("rejection", role=None))
    [app] = repo.list_applications()
    assert app.status is ApplicationStatus.REJECTED


def test_role_less_reply_is_not_guessed_when_the_company_has_two_applications(
    repo: Repository,
) -> None:
    repo.record(message("m1", "t1"), _assess(role="Data Engineer"))
    repo.record(message("m2", "t2"), _assess(role="ML Engineer"))
    repo.record(message("m3", "t3", days=1), _assess("rejection", role=None))
    assert len(repo.list_applications()) == 3


def test_role_learned_later_is_filled_in(repo: Repository) -> None:
    repo.record(message("m1", "t1"), _assess(role=None))
    repo.record(message("m2", "t1", days=1), _assess("interview_invitation", role="ML Engineer"))
    [app] = repo.list_applications()
    assert app.role == "ML Engineer"


def test_out_of_order_messages_give_the_same_status_and_dates(repo: Repository) -> None:
    repo.record(message("late", "t1", days=5), _assess("rejection"))
    repo.record(message("early", "t1", days=1), _assess("interview_invitation"))

    [app] = repo.list_applications()
    assert app.status is ApplicationStatus.REJECTED
    assert app.first_seen_at < app.last_activity_at
    assert app.last_activity_at == message("late", days=5).sent_at


def test_noise_creates_no_application_but_is_remembered(repo: Repository) -> None:
    result = repo.record(message("m1"), _assess(is_job=False, company=None, role=None))
    assert result.application_id is None
    assert repo.list_applications() == []
    assert repo.processed_message_ids(["m1", "other"]) == {"m1"}


def test_latest_event_time(repo: Repository) -> None:
    assert repo.latest_event_time() is None
    repo.record(message("m1", days=3), _assess())
    assert repo.latest_event_time() == message("x", days=3).sent_at

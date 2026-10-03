"""Idempotent recording and application matching."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from apply_agent.domain import ApplicationStatus, MessageAssessment
from apply_agent.storage import init_db, make_session_factory
from apply_agent.storage.repository import ApplicationNotFoundError, Repository
from tests.agent.helpers import T0, assessment, message


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


def _only(repo: Repository) -> Any:
    [app] = repo.list_applications()
    return app


def test_a_manual_status_replaces_what_earlier_emails_said(repo: Repository) -> None:
    """The false positive from a real inbox: a survey read as an information request."""
    recorded = repo.record(message("m1"), _assess(category="information_request"))
    assert recorded.application_id is not None

    fixed = repo.set_status_override(
        recorded.application_id, ApplicationStatus.APPLIED, T0 + timedelta(days=1)
    )

    assert fixed.status is ApplicationStatus.APPLIED
    assert fixed.status_overridden
    assert repo.count_by_status() == {"applied": 1}


def test_an_email_after_the_correction_still_moves_the_status(repo: Repository) -> None:
    app_id = repo.record(message("m1"), _assess(category="information_request")).application_id
    assert app_id is not None
    repo.set_status_override(app_id, ApplicationStatus.APPLIED, T0 + timedelta(days=1))

    repo.record(message("m2", days=3), _assess(category="interview_invitation"))

    assert _only(repo).status is ApplicationStatus.INTERVIEWING
    assert _only(repo).status_overridden


def test_an_older_email_processed_later_does_not_undo_the_correction(repo: Repository) -> None:
    app_id = repo.record(message("m2", days=2), _assess(category="other")).application_id
    assert app_id is not None
    repo.set_status_override(app_id, ApplicationStatus.REJECTED, T0 + timedelta(days=5))

    repo.record(message("m1", days=1), _assess(category="interview_invitation"))

    assert _only(repo).status is ApplicationStatus.REJECTED


def test_a_correction_can_be_changed_and_cleared(repo: Repository) -> None:
    app_id = repo.record(message("m1"), _assess(category="interview_invitation")).application_id
    assert app_id is not None
    repo.set_status_override(app_id, ApplicationStatus.APPLIED, T0 + timedelta(days=1))
    repo.set_status_override(app_id, ApplicationStatus.REJECTED, T0 + timedelta(days=2))
    assert _only(repo).status is ApplicationStatus.REJECTED

    cleared = repo.clear_status_override(app_id)

    assert cleared.status is ApplicationStatus.INTERVIEWING
    assert not cleared.status_overridden
    assert repo.clear_status_override(app_id).status is ApplicationStatus.INTERVIEWING


def test_correcting_an_unknown_application_fails(repo: Repository) -> None:
    with pytest.raises(ApplicationNotFoundError):
        repo.set_status_override(999, ApplicationStatus.APPLIED, T0)
    with pytest.raises(ApplicationNotFoundError):
        repo.clear_status_override(999)


def test_a_database_from_before_overrides_gains_the_table(engine: Engine, repo: Repository) -> None:
    """No migrations (D9): the new table must appear on an existing database."""
    app_id = repo.record(message("m1"), _assess(category="information_request")).application_id
    assert app_id is not None
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE status_overrides"))

    init_db(engine)

    assert repo.set_status_override(app_id, ApplicationStatus.APPLIED, T0).status_overridden

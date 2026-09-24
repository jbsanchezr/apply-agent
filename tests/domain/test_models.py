from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from apply_agent.domain import Application, ApplicationStatus, Event, Message, MessageCategory

CEST = timezone(timedelta(hours=2))
T0 = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "id": "m1",
        "thread_id": "t1",
        "subject": "Hello",
        "sender": "Recruiter <r@acme.example>",
        "sent_at": T0,
        "body_text": "Hi",
    }
    return Message.model_validate(fields | overrides)


def test_message_normalises_timestamps_to_utc() -> None:
    msg = _message(sent_at=datetime(2026, 7, 1, 11, 0, tzinfo=CEST))
    assert msg.sent_at == T0
    assert msg.sent_at.tzinfo == UTC


def test_message_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _message(sent_at=datetime(2026, 7, 1, 9, 0))  # noqa: DTZ001 - the point of the test


def test_message_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        _message(labels=["INBOX"])


def test_message_is_immutable() -> None:
    msg = _message()
    with pytest.raises(ValidationError, match="frozen"):
        msg.subject = "changed"  # type: ignore[misc]


def test_event_summary_must_be_one_line() -> None:
    with pytest.raises(ValidationError, match="single line"):
        Event(
            message_id="m1",
            thread_id="t1",
            category=MessageCategory.REJECTION,
            occurred_at=T0,
            summary="Rejected.\nSee details.",
        )


def test_event_category_rejects_unknown_label() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "message_id": "m1",
                "thread_id": "t1",
                "category": "hired",
                "occurred_at": T0,
                "summary": "Hired",
            }
        )


def test_application_activity_cannot_precede_first_seen() -> None:
    with pytest.raises(ValidationError, match="last_activity_at"):
        Application(
            company="Acme",
            status=ApplicationStatus.APPLIED,
            first_seen_at=T0,
            last_activity_at=T0 - timedelta(days=1),
            thread_ids=frozenset({"t1"}),
        )


def test_application_requires_at_least_one_thread() -> None:
    with pytest.raises(ValidationError, match="thread_ids"):
        Application(
            company="Acme",
            status=ApplicationStatus.APPLIED,
            first_seen_at=T0,
            last_activity_at=T0,
            thread_ids=frozenset(),
        )


def test_application_strips_whitespace() -> None:
    app = Application(
        company="  Acme  ",
        role=" Backend Engineer ",
        status=ApplicationStatus.APPLIED,
        first_seen_at=T0,
        last_activity_at=T0,
        thread_ids=frozenset({"t1"}),
    )
    assert (app.company, app.role) == ("Acme", "Backend Engineer")

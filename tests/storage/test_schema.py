from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from apply_agent.domain import ApplicationStatus, MessageCategory, company_key, role_key
from apply_agent.storage import init_db
from apply_agent.storage.schema import ApplicationRow, ApplicationThreadRow, EventRow

T0 = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _application(company: str = "Acme", role: str | None = "Backend Engineer") -> ApplicationRow:
    return ApplicationRow(
        company=company,
        company_key=company_key(company),
        role=role,
        role_key=role_key(role),
        status=ApplicationStatus.APPLIED,
        first_seen_at=T0,
        last_activity_at=T0,
    )


def _event(message_id: str = "m1", application_id: int | None = None) -> EventRow:
    return EventRow(
        message_id=message_id,
        thread_id="t1",
        application_id=application_id,
        category=MessageCategory.OTHER,
        occurred_at=T0,
        summary="Acknowledgement",
    )


def test_expected_tables_exist(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == {
        "applications",
        "application_threads",
        "events",
        "follow_up_drafts",
        "status_overrides",
    }


def test_init_db_is_idempotent(engine: Engine) -> None:
    init_db(engine)
    init_db(engine)


def test_same_message_cannot_be_recorded_twice(session: Session) -> None:
    session.add(_event("m1"))
    session.commit()
    session.add(_event("m1"))
    with pytest.raises(IntegrityError, match=r"events\.message_id"):
        session.commit()


def test_noise_events_need_no_application(session: Session) -> None:
    session.add(_event(application_id=None))
    session.commit()
    assert session.scalars(select(EventRow)).one().application_id is None


def test_company_and_role_identify_an_application(session: Session) -> None:
    session.add(_application("Kestrel Robotics GmbH", "Backend Engineer"))
    session.commit()
    session.add(_application("kestrel robotics", "backend engineer"))
    with pytest.raises(IntegrityError, match="company_key"):
        session.commit()


def test_unknown_role_still_counts_for_uniqueness(session: Session) -> None:
    session.add(_application("Acme", None))
    session.commit()
    session.add(_application("ACME", None))
    with pytest.raises(IntegrityError):
        session.commit()


def test_thread_belongs_to_one_application(session: Session) -> None:
    first, second = _application("Acme"), _application("Globex")
    first.threads.append(ApplicationThreadRow(thread_id="t1"))
    session.add_all([first, second])
    session.commit()
    second_id = second.id
    session.expunge_all()  # bypass the identity map so the database has to say no

    session.add(ApplicationThreadRow(thread_id="t1", application_id=second_id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_foreign_keys_are_enforced(session: Session) -> None:
    assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    session.add(_event(application_id=999))
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.commit()


def test_status_is_stored_as_its_value(session: Session) -> None:
    session.add(_application())
    session.commit()
    raw = session.execute(text("SELECT status FROM applications")).scalar_one()
    assert raw == "applied"


def test_invalid_status_is_rejected_by_the_database(session: Session) -> None:
    session.add(_application())
    session.commit()
    with pytest.raises(IntegrityError, match="CHECK"):
        session.execute(text("UPDATE applications SET status = 'hired'"))


def test_datetimes_round_trip_as_aware_utc(session: Session) -> None:
    cest = timezone(timedelta(hours=2))
    row = _event()
    row.occurred_at = datetime(2026, 7, 1, 11, 0, tzinfo=cest)
    session.add(row)
    session.commit()
    session.expunge_all()

    stored = session.scalars(select(EventRow)).one()
    assert stored.occurred_at == T0
    assert stored.occurred_at.tzinfo == UTC


def test_naive_datetimes_are_refused(session: Session) -> None:
    row = _event()
    row.occurred_at = datetime(2026, 7, 1, 9, 0)  # noqa: DTZ001 - the point of the test
    session.add(row)
    with pytest.raises(StatementError, match="naive"):
        session.commit()


def test_deleting_an_application_keeps_its_events(session: Session) -> None:
    app = _application()
    app.threads.append(ApplicationThreadRow(thread_id="t1"))
    session.add(app)
    session.flush()
    session.add(_event(application_id=app.id))
    session.commit()

    session.delete(app)
    session.commit()

    assert session.scalars(select(ApplicationThreadRow)).all() == []
    assert session.scalars(select(EventRow)).one().application_id is None

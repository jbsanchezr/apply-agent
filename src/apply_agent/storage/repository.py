"""Persistence operations used by the agent and the API.

``record`` is the single write path. It runs in one transaction and is
idempotent: recording a message a second time is a no-op that reports the
existing outcome.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain import (
    Application,
    Event,
    Message,
    MessageAssessment,
    company_key,
    derive_status,
    role_key,
)
from apply_agent.domain.enums import ApplicationStatus
from apply_agent.storage.matching import resolve_application
from apply_agent.storage.schema import ApplicationRow, ApplicationThreadRow, EventRow


@dataclass(frozen=True, slots=True)
class ApplicationView:
    """An application plus its latest event, for display."""

    application: Application
    latest_summary: str
    latest_category: str


@dataclass(frozen=True, slots=True)
class RecordResult:
    application_id: int | None
    status: ApplicationStatus | None
    created_application: bool
    duplicate: bool


class Repository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def processed_message_ids(self, candidates: Collection[str]) -> set[str]:
        if not candidates:
            return set()
        with self._sessions() as session:
            query = select(EventRow.message_id).where(EventRow.message_id.in_(candidates))
            return set(session.scalars(query))

    def latest_event_time(self) -> datetime | None:
        with self._sessions() as session:
            return session.scalar(select(func.max(EventRow.occurred_at)))

    def record(self, message: Message, assessment: MessageAssessment) -> RecordResult:
        with self._sessions.begin() as session:
            existing = session.scalar(select(EventRow).where(EventRow.message_id == message.id))
            if existing is not None:
                app = existing.application
                return RecordResult(
                    app.id if app else None, app.status if app else None, False, duplicate=True
                )

            app, created = None, False
            if assessment.is_job_application:
                app, created = self._application_for(session, message, assessment)

            session.add(
                EventRow(
                    message_id=message.id,
                    thread_id=message.thread_id,
                    application=app,
                    category=assessment.category,
                    occurred_at=message.sent_at,
                    summary=assessment.summary,
                )
            )
            session.flush()
            if app is not None:
                _refresh(app)
            return RecordResult(
                app.id if app else None, app.status if app else None, created, duplicate=False
            )

    def count_by_status(self) -> dict[str, int]:
        with self._sessions() as session:
            rows = session.execute(
                select(ApplicationRow.status, func.count()).group_by(ApplicationRow.status)
            )
            return {status.value: count for status, count in rows}

    def list_application_views(self) -> list[ApplicationView]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ApplicationRow).order_by(ApplicationRow.last_activity_at.desc())
            )
            views = []
            for row in rows:
                latest = max(row.events, key=lambda e: (e.occurred_at, e.message_id))
                views.append(
                    ApplicationView(_to_domain(row), latest.summary, latest.category.value)
                )
            return views

    def list_applications(self) -> list[Application]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ApplicationRow).order_by(ApplicationRow.last_activity_at.desc())
            )
            return [_to_domain(row) for row in rows]

    def _application_for(
        self, session: Session, message: Message, assessment: MessageAssessment
    ) -> tuple[ApplicationRow, bool]:
        if assessment.company is None:  # MessageAssessment already forbids this
            raise ValueError("a job application needs a company")
        app = resolve_application(session, message.thread_id, assessment.company, assessment.role)
        created = app is None
        if app is None:
            app = ApplicationRow(
                company=assessment.company,
                company_key=company_key(assessment.company),
                role=assessment.role,
                role_key=role_key(assessment.role),
                status=ApplicationStatus.APPLIED,
                first_seen_at=message.sent_at,
                last_activity_at=message.sent_at,
            )
            session.add(app)
        elif app.role is None and assessment.role is not None:
            _fill_role(session, app, assessment.role)
        if session.get(ApplicationThreadRow, message.thread_id) is None:
            app.threads.append(ApplicationThreadRow(thread_id=message.thread_id))
        return app, created


def _fill_role(session: Session, app: ApplicationRow, role: str) -> None:
    """Learn the role later (e.g. a role-less acknowledgement, then a detailed reply)."""
    clash = session.scalar(
        select(ApplicationRow.id).where(
            ApplicationRow.company_key == app.company_key,
            ApplicationRow.role_key == role_key(role),
        )
    )
    if clash is None:
        app.role, app.role_key = role, role_key(role)


def _refresh(app: ApplicationRow) -> None:
    """Recompute the derived fields from the full event history (see D3)."""
    events = [_event(e) for e in app.events]
    app.status = derive_status(events)
    app.first_seen_at = min(e.occurred_at for e in events)
    app.last_activity_at = max(e.occurred_at for e in events)


def _event(row: EventRow) -> Event:
    return Event(
        message_id=row.message_id,
        thread_id=row.thread_id,
        category=row.category,
        occurred_at=row.occurred_at,
        summary=row.summary,
    )


def _to_domain(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        company=row.company,
        role=row.role,
        status=row.status,
        first_seen_at=row.first_seen_at,
        last_activity_at=row.last_activity_at,
        thread_ids=frozenset(t.thread_id for t in row.threads),
    )

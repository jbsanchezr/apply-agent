"""Persistence operations used by the agent and the API.

``record`` is the single write path. It runs in one transaction and is
idempotent: recording a message a second time is a no-op that reports the
existing outcome.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain import (
    Application,
    Event,
    Message,
    MessageAssessment,
    StatusOverride,
    company_key,
    derive_status,
    role_key,
)
from apply_agent.domain.enums import ApplicationStatus
from apply_agent.storage.matching import resolve_application
from apply_agent.storage.schema import (
    ApplicationAliasRow,
    ApplicationRow,
    ApplicationThreadRow,
    EventRow,
    FollowUpDraftRow,
    StatusOverrideRow,
)


class ApplicationNotFoundError(LookupError):
    """No application has this id."""


@dataclass(frozen=True, slots=True)
class ApplicationView:
    """An application plus the emails behind it, newest first, for display."""

    application: Application
    latest_summary: str
    latest_category: str
    events: tuple[Event, ...] = ()


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
                events = sorted(
                    (_event(e) for e in row.events),
                    key=lambda e: (e.occurred_at, e.message_id),
                    reverse=True,
                )
                latest = events[0]
                views.append(
                    ApplicationView(
                        _to_domain(row), latest.summary, latest.category.value, tuple(events)
                    )
                )
            return views

    def list_applications(self) -> list[Application]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ApplicationRow).order_by(ApplicationRow.last_activity_at.desc())
            )
            return [_to_domain(row) for row in rows]

    def set_status_override(
        self, application_id: int, status: ApplicationStatus, at: datetime
    ) -> Application:
        """Correct an application's status by hand. Emails dated after ``at`` still move it."""
        with self._sessions.begin() as session:
            app = _get(session, application_id)
            if app.override is None:
                app.override = StatusOverrideRow(status=status, set_at=at)
            else:
                app.override.status, app.override.set_at = status, at
            _refresh(app)
            return _to_domain(app)

    def clear_status_override(self, application_id: int) -> Application:
        """Go back to the status the emails imply."""
        with self._sessions.begin() as session:
            app = _get(session, application_id)
            app.override = None
            _refresh(app)
            return _to_domain(app)

    def merge_applications(self, source_id: int, into_id: int) -> Application:
        """Fold ``source_id`` into ``into_id``: the user says they are one application.

        The target keeps its company and role. The source's emails, threads and
        drafts move to it, and its spelling becomes an alias so later emails
        follow. Of two manual corrections, the more recent one is kept.
        """
        if source_id == into_id:
            raise ValueError("an application cannot be merged into itself")
        with self._sessions.begin() as session:
            source, target = _get(session, source_id), _get(session, into_id)
            alias_key = (source.company_key, source.role_key)
            mine, theirs = source.override, target.override
            if mine is not None and theirs is None:
                target.override = StatusOverrideRow(status=mine.status, set_at=mine.set_at)
            elif mine is not None and theirs is not None and mine.set_at > theirs.set_at:
                theirs.status, theirs.set_at = mine.status, mine.set_at
            session.flush()
            for table in (EventRow, ApplicationThreadRow, FollowUpDraftRow, ApplicationAliasRow):
                session.execute(
                    update(table)
                    .where(table.application_id == source_id)
                    .values(application_id=into_id)
                )
            session.execute(delete(ApplicationRow).where(ApplicationRow.id == source_id))
            session.expire_all()
            session.merge(
                ApplicationAliasRow(
                    company_key=alias_key[0], role_key=alias_key[1], application_id=into_id
                )
            )
            target = _get(session, into_id)
            _refresh(target)
            return _to_domain(target)

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


def _get(session: Session, application_id: int) -> ApplicationRow:
    app = session.get(ApplicationRow, application_id)
    if app is None:
        raise ApplicationNotFoundError(application_id)
    return app


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
    override = (
        None
        if app.override is None
        else StatusOverride(status=app.override.status, set_at=app.override.set_at)
    )
    app.status = derive_status(events, override)
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
        status_overridden=row.override is not None,
        first_seen_at=row.first_seen_at,
        last_activity_at=row.last_activity_at,
        thread_ids=frozenset(t.thread_id for t in row.threads),
    )

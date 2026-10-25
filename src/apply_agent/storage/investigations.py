"""Persistence for web investigations of companies (D67)."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain.keys import company_key
from apply_agent.storage.schema import InvestigationRow


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Source:
    number: int
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class Investigation:
    id: int
    company: str
    context: str | None
    question: str | None
    status: InvestigationStatus
    report: str | None
    sources: tuple[Source, ...]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class InvestigationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def queue(self, company: str, context: str | None, question: str | None) -> int:
        with self._sessions.begin() as session:
            row = InvestigationRow(
                company=company[:100],
                company_key=company_key(company)[:100],
                context=(context or None) and context[:100],
                question=(question or None) and question[:300],
                status=InvestigationStatus.QUEUED.value,
            )
            session.add(row)
            session.flush()
            return row.id

    def next_queued(self) -> Investigation | None:
        """The oldest queued investigation, marked as running."""
        with self._sessions.begin() as session:
            row = session.scalar(
                select(InvestigationRow)
                .where(InvestigationRow.status == InvestigationStatus.QUEUED.value)
                .order_by(InvestigationRow.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = InvestigationStatus.RUNNING.value
            return _to_domain(row)

    def finish(
        self, investigation_id: int, report: str, sources: list[Source], at: datetime
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(InvestigationRow, investigation_id)
            if row is not None:
                row.status, row.report, row.finished_at = InvestigationStatus.DONE.value, report, at
                row.sources = json.dumps([[s.number, s.title, s.url] for s in sources])

    def fail(self, investigation_id: int, error: str, at: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(InvestigationRow, investigation_id)
            if row is not None:
                row.status, row.error = InvestigationStatus.FAILED.value, error[:300]
                row.finished_at = at

    def get(self, investigation_id: int) -> Investigation | None:
        with self._sessions() as session:
            row = session.get(InvestigationRow, investigation_id)
            return None if row is None else _to_domain(row)

    def for_company(self, company: str, *, limit: int = 5) -> list[Investigation]:
        with self._sessions() as session:
            rows = session.scalars(
                select(InvestigationRow)
                .where(InvestigationRow.company_key == company_key(company))
                .order_by(InvestigationRow.id.desc())
                .limit(limit)
            )
            return [_to_domain(row) for row in rows]


def _to_domain(row: InvestigationRow) -> Investigation:
    raw = json.loads(row.sources) if row.sources else []
    return Investigation(
        id=row.id,
        company=row.company,
        context=row.context,
        question=row.question,
        status=InvestigationStatus(row.status),
        report=row.report,
        sources=tuple(Source(int(n), str(t), str(u)) for n, t, u in raw),
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )

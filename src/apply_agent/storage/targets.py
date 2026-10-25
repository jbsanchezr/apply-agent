"""Persistence for the user's list of target companies (D66)."""

import re
from datetime import datetime
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain.keys import company_key
from apply_agent.domain.search import BoardStatus, TargetCompany
from apply_agent.storage.schema import TargetCompanyRow

_SEPARATORS: Final = re.compile(r"\s*(?:,|;|\n|\s+(?:y|and|e|und|et)\s+)\s*", re.IGNORECASE)
MAX_NAME: Final = 100


def parse_company_list(text: str) -> list[tuple[str | None, str]]:
    """'Netherlands: ASML, Adyen y ING' -> [('Netherlands', 'ASML'), ...].

    One group per line, before a colon; names separated by commas,
    semicolons or "and" in a few languages. A trailing full stop is dropped,
    so a list pasted from a document reads as written.
    """
    found: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        group, _, names = line.rpartition(":") if ":" in line else ("", "", line)
        label = group.strip()[:60] or None
        for raw in _SEPARATORS.split(names.strip().rstrip(".")):
            name = raw.strip().strip(".").strip()[:MAX_NAME]
            key = company_key(name)
            if name and key and key not in seen:
                seen.add(key)
                found.append((label, name))
    return found


class TargetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def add(self, companies: list[tuple[str | None, str]]) -> int:
        """Add companies not on the list yet. Returns how many were added."""
        added = 0
        with self._sessions.begin() as session:
            for group, name in companies:
                key = company_key(name)
                exists = session.scalar(
                    select(TargetCompanyRow.id).where(TargetCompanyRow.name_key == key)
                )
                if exists is None:
                    session.add(TargetCompanyRow(name=name, name_key=key, group_label=group))
                    added += 1
        return added

    def list(self) -> list[TargetCompany]:
        with self._sessions() as session:
            rows = session.scalars(select(TargetCompanyRow).order_by(TargetCompanyRow.id)).all()
            return [_to_domain(row) for row in rows]

    def get(self, target_id: int) -> TargetCompany | None:
        with self._sessions() as session:
            row = session.get(TargetCompanyRow, target_id)
            return None if row is None else _to_domain(row)

    def record(
        self,
        target_id: int,
        status: BoardStatus,
        at: datetime,
        *,
        board: str | None = None,
        slug: str | None = None,
        note: str | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(TargetCompanyRow, target_id)
            if row is not None:
                row.status, row.checked_at = status.value, at
                row.board, row.slug, row.note = board, slug, (note or None) and note[:300]

    def set_context(self, target_id: int, context: str | None) -> None:
        with self._sessions.begin() as session:
            row = session.get(TargetCompanyRow, target_id)
            if row is not None:
                row.context = (context or "").strip()[:100] or None

    def remove(self, target_id: int) -> bool:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(TargetCompanyRow).where(TargetCompanyRow.id == target_id)
            )
            return bool(getattr(result, "rowcount", 0))


def _to_domain(row: TargetCompanyRow) -> TargetCompany:
    return TargetCompany(
        id=row.id,
        name=row.name,
        group=row.group_label,
        context=row.context,
        status=BoardStatus(row.status),
        board=row.board,
        slug=row.slug,
        note=row.note,
        checked_at=row.checked_at,
    )

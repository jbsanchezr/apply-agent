"""Persistence for saved searches and watched companies (D62)."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain.search import JobSearch, SavedSearch, WatchedCompany
from apply_agent.storage.schema import SavedSearchRow, WatchedCompanyRow


def _words(text: str | None) -> tuple[str, ...]:
    return tuple(w.strip() for w in (text or "").split(",") if w.strip())


def _joined(words: Sequence[str]) -> str | None:
    return ", ".join(words) or None


class SearchRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def list_searches(self) -> list[SavedSearch]:
        with self._sessions() as session:
            rows = session.scalars(select(SavedSearchRow).order_by(SavedSearchRow.id))
            return [
                SavedSearch(
                    id=row.id,
                    search=JobSearch.model_validate(
                        {
                            "what": row.what,
                            "where": row.where,
                            "country": row.country,
                            "company": row.company,
                            "max_days_old": row.max_days_old,
                        }
                    ),
                )
                for row in rows
            ]

    def save_search(self, search: JobSearch) -> int:
        with self._sessions.begin() as session:
            row = SavedSearchRow(
                what=search.what,
                where=search.where,
                country=search.country,
                company=search.company,
                max_days_old=search.max_days_old,
            )
            session.add(row)
            session.flush()
            return row.id

    def delete_search(self, search_id: int) -> bool:
        with self._sessions.begin() as session:
            result = session.execute(delete(SavedSearchRow).where(SavedSearchRow.id == search_id))
            return bool(getattr(result, "rowcount", 0))

    def list_companies(self) -> list[WatchedCompany]:
        with self._sessions() as session:
            rows = session.scalars(select(WatchedCompanyRow).order_by(WatchedCompanyRow.name))
            return [
                WatchedCompany(
                    id=row.id,
                    name=row.name,
                    board=row.board,
                    slug=row.slug,
                    keywords=_words(row.keywords),
                    places=_words(row.places),
                    last_checked_at=row.last_checked_at,
                    last_error=row.last_error,
                )
                for row in rows
            ]

    def watch_company(
        self, name: str, board: str, slug: str, keywords: Sequence[str], places: Sequence[str]
    ) -> int:
        """Watch a company, or update the filters of one already watched on that board."""
        with self._sessions.begin() as session:
            row = session.scalar(
                select(WatchedCompanyRow).where(
                    WatchedCompanyRow.board == board, WatchedCompanyRow.slug == slug
                )
            )
            if row is None:
                row = WatchedCompanyRow(name=name, board=board, slug=slug)
                session.add(row)
            row.keywords, row.places = _joined(keywords), _joined(places)
            session.flush()
            return row.id

    def unwatch_company(self, company_id: int) -> bool:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(WatchedCompanyRow).where(WatchedCompanyRow.id == company_id)
            )
            return bool(getattr(result, "rowcount", 0))

    def record_check(self, company_id: int, at: datetime, error: str | None) -> None:
        with self._sessions.begin() as session:
            row = session.get(WatchedCompanyRow, company_id)
            if row is not None:
                row.last_checked_at, row.last_error = at, (error or None) and error[:300]

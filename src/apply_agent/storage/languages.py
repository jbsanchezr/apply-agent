"""Languages companies asked for, as the user noted them (D69)."""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain.keys import company_key
from apply_agent.storage.schema import CompanyLanguageRow


class CompanyLanguages:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def note(self, company: str, language: str, at: datetime) -> None:
        """Remember that the company asked for the language. Noting it twice is fine."""
        key = company_key(company)
        with self._sessions.begin() as session:
            exists = session.scalar(
                select(CompanyLanguageRow.id).where(
                    CompanyLanguageRow.company_key == key, CompanyLanguageRow.language == language
                )
            )
            if exists is None:
                session.add(
                    CompanyLanguageRow(
                        company=company[:200], company_key=key, language=language, noted_at=at
                    )
                )

    def by_company(self) -> dict[str, tuple[str, ...]]:
        """Company key -> the languages it asked for."""
        found: dict[str, list[str]] = defaultdict(list)
        with self._sessions() as session:
            for row in session.scalars(select(CompanyLanguageRow).order_by(CompanyLanguageRow.id)):
                found[row.company_key].append(row.language)
        return {key: tuple(languages) for key, languages in found.items()}

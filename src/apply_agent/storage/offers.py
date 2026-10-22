"""Persistence for job offers and for the alert emails already scanned (D57)."""

from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.domain.offers import JobOffer, OfferState, StoredOffer
from apply_agent.storage.schema import JobOfferRow, ScannedAlertRow


class OfferNotFoundError(LookupError):
    """No offer has this id."""


class OfferRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def save(self, offers: Iterable[JobOffer]) -> int:
        """Insert new offers and refresh known ones. Returns how many were new.

        A posting seen again keeps the user's decision about it; its details
        are updated, because a search result is richer than an alert line.
        """
        created = 0
        with self._sessions.begin() as session:
            for offer in offers:
                row = session.scalar(select(JobOfferRow).where(JobOfferRow.offer_key == offer.key))
                if row is None:
                    session.add(
                        JobOfferRow(
                            offer_key=offer.key,
                            source=offer.source,
                            linkedin_id=offer.linkedin_id,
                            title=offer.title,
                            company=offer.company,
                            location=offer.location,
                            url=offer.url,
                            posted_on=offer.posted_on,
                            state=OfferState.NEW,
                            first_seen_at=offer.seen_at,
                            last_seen_at=offer.seen_at,
                        )
                    )
                    # Flushed so a posting listed twice in one batch is found the second time.
                    session.flush()
                    created += 1
                    continue
                row.location = offer.location or row.location
                row.url = offer.url or row.url
                row.posted_on = offer.posted_on or row.posted_on
                row.first_seen_at = min(row.first_seen_at, offer.seen_at)
                row.last_seen_at = max(row.last_seen_at, offer.seen_at)
        return created

    def list_offers(self) -> list[StoredOffer]:
        with self._sessions() as session:
            rows = session.scalars(
                select(JobOfferRow).order_by(JobOfferRow.last_seen_at.desc(), JobOfferRow.id)
            )
            return [_to_domain(row) for row in rows]

    def set_state(self, offer_id: int, state: OfferState) -> StoredOffer:
        with self._sessions.begin() as session:
            row = session.get(JobOfferRow, offer_id)
            if row is None:
                raise OfferNotFoundError(offer_id)
            row.state = state
            return _to_domain(row)

    def scanned_message_ids(self, candidates: Collection[str]) -> set[str]:
        if not candidates:
            return set()
        with self._sessions() as session:
            query = select(ScannedAlertRow.message_id).where(
                ScannedAlertRow.message_id.in_(candidates)
            )
            return set(session.scalars(query))

    def latest_scanned_time(self) -> datetime | None:
        with self._sessions() as session:
            return session.scalar(select(func.max(ScannedAlertRow.sent_at)))

    def forget_scanned(self) -> int:
        """Forget which emails were scanned, so the next scan reads them again."""
        with self._sessions.begin() as session:
            return _deleted(session.execute(delete(ScannedAlertRow)))

    def delete_offers(self, offer_ids: Collection[int]) -> int:
        if not offer_ids:
            return 0
        with self._sessions.begin() as session:
            return _deleted(
                session.execute(delete(JobOfferRow).where(JobOfferRow.id.in_(offer_ids)))
            )

    def mark_scanned(self, message_id: str, sent_at: datetime, offers_found: int) -> None:
        with self._sessions.begin() as session:
            session.merge(
                ScannedAlertRow(message_id=message_id, sent_at=sent_at, offers_found=offers_found)
            )


def _deleted(result: Result[Any]) -> int:
    """Rows a DELETE removed. Its result is always a cursor result."""
    return cast(CursorResult[Any], result).rowcount


def _to_domain(row: JobOfferRow) -> StoredOffer:
    return StoredOffer(
        id=row.id,
        offer=JobOffer(
            source=row.source,
            linkedin_id=row.linkedin_id,
            title=row.title,
            company=row.company,
            location=row.location,
            url=row.url,
            posted_on=row.posted_on,
            seen_at=row.first_seen_at,
        ),
        state=row.state,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )

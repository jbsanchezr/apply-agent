"""Persistence for job offers and for the alert emails already scanned (D57)."""

from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, delete, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from apply_agent.domain.offers import JobOffer, OfferState, StoredOffer
from apply_agent.storage.schema import JobOfferRow, OfferClosureRow, ScannedAlertRow


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
                            external_id=offer.external_id,
                            description=offer.description,
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
                row.description = offer.description or row.description
                if row.closure is not None and row.closure.detected:
                    # Listed again by its board: it was not closed after all.
                    row.closure = None
                row.first_seen_at = min(row.first_seen_at, offer.seen_at)
                row.last_seen_at = max(row.last_seen_at, offer.seen_at)
        return created

    def known_keys(self, keys: Collection[str]) -> set[str]:
        """Which of these offer keys are already stored."""
        if not keys:
            return set()
        with self._sessions() as session:
            query = select(JobOfferRow.offer_key).where(JobOfferRow.offer_key.in_(keys))
            return set(session.scalars(query))

    def list_offers(self) -> list[StoredOffer]:
        with self._sessions() as session:
            rows = session.scalars(
                select(JobOfferRow)
                .options(selectinload(JobOfferRow.closure))
                .order_by(JobOfferRow.last_seen_at.desc(), JobOfferRow.id)
            )
            return [_to_domain(row) for row in rows]

    def set_state(self, offer_id: int, state: OfferState) -> StoredOffer:
        with self._sessions.begin() as session:
            row = _get(session, offer_id)
            row.state = state
            return _to_domain(row)

    def mark_closed(self, offer_id: int, at: datetime) -> StoredOffer:
        """The user found the posting no longer open. Its age then is remembered."""
        with self._sessions.begin() as session:
            row = _get(session, offer_id)
            # Measured like the fold on the page: from the last alert that listed it.
            age = max((at - row.last_seen_at).days, 0)
            if row.closure is None:
                row.closure = OfferClosureRow(reported_at=at, age_days=age)
            else:
                row.closure.reported_at, row.closure.age_days = at, age
            return _to_domain(row)

    def reopen(self, offer_id: int) -> StoredOffer:
        """Undo a closure marked by mistake."""
        with self._sessions.begin() as session:
            row = _get(session, offer_id)
            row.closure = None
            return _to_domain(row)

    def close_missing(self, id_prefix: str, open_ids: Collection[str], at: datetime) -> int:
        """Close offers from one board that its fresh listing no longer has (D62).

        ``open_ids`` is the whole listing, before any filter, so an offer the
        user's filters now hide is not taken for a closed one.
        """
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(JobOfferRow).where(
                    JobOfferRow.external_id.startswith(id_prefix, autoescape=True),
                    ~JobOfferRow.closure.has(),
                )
            )
            gone = [row for row in rows if row.external_id not in open_ids]
            for row in gone:
                age = max((at - row.first_seen_at).days, 0)
                row.closure = OfferClosureRow(reported_at=at, age_days=age, detected=True)
            return len(gone)

    def closing_ages(self) -> list[int]:
        """Days old each offer was when the user found it closed (not board closures)."""
        with self._sessions() as session:
            query = select(OfferClosureRow.age_days).where(OfferClosureRow.detected.is_(False))
            return list(session.scalars(query))

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


def _get(session: Session, offer_id: int) -> JobOfferRow:
    row = session.get(JobOfferRow, offer_id)
    if row is None:
        raise OfferNotFoundError(offer_id)
    return row


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
            external_id=row.external_id,
            description=row.description,
        ),
        state=row.state,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        closed_at=row.closure.reported_at if row.closure else None,
    )

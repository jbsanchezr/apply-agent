"""Find job offers: LinkedIn alert emails, and optionally LinkedIn's public search.

The scanner is independent of the agent. It keeps its own record of the
emails it has read, asks the provider only for LinkedIn mail, and never calls
a model, so a scan costs no tokens and cannot touch an application.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx

from apply_agent.config import LinkedInSearch, Settings
from apply_agent.offers.alerts import (
    PROVIDER_SENDER_FILTER,
    is_hint,
    is_linkedin_alert,
    parse_linkedin_alert,
)
from apply_agent.offers.linkedin_search import LinkedInGuestSearch, SearchRefusedError, make_client
from apply_agent.providers import EmailProvider, make_provider
from apply_agent.storage import init_db, make_engine, make_session_factory
from apply_agent.storage.offers import OfferRepository

logger = logging.getLogger(__name__)

# Like the agent's sync (see SYNC_OVERLAP), re-list a few days to catch late mail.
SCAN_OVERLAP: Final = timedelta(days=3)


@dataclass(frozen=True, slots=True)
class OfferScanReport:
    alert_emails: int = 0
    offers_found: int = 0
    new_offers: int = 0
    searches_run: int = 0
    search_errors: list[str] = field(default_factory=list)


class OfferScanner:
    def __init__(
        self,
        provider: EmailProvider,
        offers: OfferRepository,
        *,
        initial_lookback: timedelta,
        searches: tuple[LinkedInSearch, ...] = (),
        search: LinkedInGuestSearch | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self._offers = offers
        self._initial_lookback = initial_lookback
        self._searches = searches
        self._search = search
        self._clock = clock

    def scan(self) -> OfferScanReport:
        alerts, from_alerts, new_from_alerts = self._scan_alerts()
        runs, from_search, new_from_search, errors = self._run_searches()
        report = OfferScanReport(
            alert_emails=alerts,
            offers_found=from_alerts + from_search,
            new_offers=new_from_alerts + new_from_search,
            searches_run=runs,
            search_errors=errors,
        )
        logger.info(
            "offer scan finished",
            extra={"offers": report.offers_found, "new": report.new_offers, "alerts": alerts},
        )
        return report

    def rescan(self) -> tuple[int, OfferScanReport]:
        """Read every alert in the lookback window again, after a reader fix (D58).

        Offers whose title or company is one of LinkedIn's hints were misread
        by the first reader and are deleted; the rescan adds them back
        correctly. Every other offer, and the user's decision on it, is kept.
        """
        misread = [
            stored.id
            for stored in self._offers.list_offers()
            if is_hint(stored.offer.title) or is_hint(stored.offer.company)
        ]
        removed = self._offers.delete_offers(misread)
        self._offers.forget_scanned()
        return removed, self.scan()

    def _scan_alerts(self) -> tuple[int, int, int]:
        latest = self._offers.latest_scanned_time()
        since = self._clock() - self._initial_lookback if latest is None else latest - SCAN_OVERLAP
        scanned = self._offers.scanned_message_ids
        listed = self._provider.list_messages(
            since=since, already_have=scanned, sender=PROVIDER_SENDER_FILTER
        )
        done = scanned([m.id for m in listed])
        alerts = found = created = 0
        for message in listed:
            if message.id in done:
                continue
            offers = parse_linkedin_alert(message)
            if is_linkedin_alert(message):
                alerts += 1
            found += len(offers)
            created += self._offers.save(offers)
            # Recorded after the offers, so a crash in between re-reads the email.
            self._offers.mark_scanned(message.id, message.sent_at, len(offers))
        return alerts, found, created

    def _run_searches(self) -> tuple[int, int, int, list[str]]:
        if self._search is None:
            return 0, 0, 0, []
        runs = found = created = 0
        errors: list[str] = []
        for query in self._searches:
            label = f"{query.keywords} @ {query.location or 'anywhere'}"
            try:
                offers = self._search.search(query)
            except SearchRefusedError as err:
                errors.append(f"{label}: {err}; the remaining searches were skipped")
                break
            except httpx.HTTPError as err:
                errors.append(f"{label}: {type(err).__name__}")
                continue
            runs += 1
            found += len(offers)
            created += self._offers.save(offers)
        for error in errors:
            logger.warning("linkedin search failed", extra={"error": error})
        return runs, found, created, errors


def build_offer_scanner(
    settings: Settings,
    offers: OfferRepository | None = None,
    *,
    provider: EmailProvider | None = None,
) -> tuple[OfferScanner, OfferRepository]:
    if offers is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        offers = OfferRepository(make_session_factory(engine))
    search = None
    if settings.linkedin_search_enabled:
        search = LinkedInGuestSearch(
            make_client(),
            pages=settings.linkedin_search_pages,
            posted_within_days=settings.linkedin_search_days,
            clock=lambda: datetime.now(UTC),
        )
    scanner = OfferScanner(
        provider or make_provider(settings),
        offers,
        initial_lookback=timedelta(days=settings.initial_lookback_days),
        searches=settings.linkedin_searches,
        search=search,
    )
    return scanner, offers

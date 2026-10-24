"""The Search page's engine: Adzuna searches and watched companies (D62).

A search typed on the page runs at once and its results join the offers. A
saved search and every watched company run again on each sync. Offers from
here go through the same fit score and applied check as alert offers; they
only carry more, a description and, for company boards, closure for free.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from apply_agent.config import Settings
from apply_agent.domain.fit import found_words
from apply_agent.domain.offers import JobOffer
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.search import JobSearch, WatchedCompany
from apply_agent.offers.adzuna import AdzunaError, AdzunaSearch
from apply_agent.offers.adzuna import make_client as make_adzuna_client
from apply_agent.offers.boards import Board, BoardError, BoardKind, CompanyBoards
from apply_agent.offers.boards import make_client as make_boards_client
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository

logger = logging.getLogger(__name__)


class SearchUnavailableError(RuntimeError):
    """Adzuna is not configured, so title searches cannot run."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    offers: tuple[JobOffer, ...]
    new: int


@dataclass(frozen=True, slots=True)
class SearchReport:
    """What a sync's searches did, for the job report."""

    searches_run: int = 0
    companies_checked: int = 0
    offers_found: int = 0
    new_offers: int = 0
    closed_detected: int = 0
    errors: list[str] = field(default_factory=list)


def keep_offer(offer: JobOffer, keywords: tuple[str, ...], places: tuple[str, ...]) -> bool:
    """A company's posting worth keeping: a wanted word in the title, in a wanted place.

    Remote postings pass any place filter.
    """
    if keywords and not found_words(keywords, offer.title):
        return False
    if not places:
        return True
    location = (offer.location or "").casefold()
    return "remote" in location or any(p.casefold() in location for p in places)


class JobSearcher:
    def __init__(
        self,
        offers: OfferRepository,
        searches: SearchRepository,
        profile: CandidateProfile,
        *,
        boards: CompanyBoards,
        adzuna: AdzunaSearch | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._offers = offers
        self._searches = searches
        self._profile = profile
        self._boards = boards
        self._adzuna = adzuna
        self._clock = clock

    @property
    def adzuna_enabled(self) -> bool:
        return self._adzuna is not None

    def search(self, search: JobSearch) -> SearchResult:
        """Run one Adzuna search now and keep its results as offers."""
        if self._adzuna is None:
            raise SearchUnavailableError("Adzuna is not configured")
        found = self._adzuna.search(search)
        return SearchResult(tuple(found), self._offers.save(found))

    def find_company(self, name: str) -> Board | None:
        return self._boards.find(name)

    def check_company(self, company: WatchedCompany) -> tuple[int, int, int]:
        """Read a watched company's board: (kept, new, closed). Raises on failure."""
        board = Board(BoardKind(company.board), company.slug)
        listing = self._boards.listing(board, company.name)
        keywords = company.keywords or self._profile.target
        kept = [o for o in listing.offers if keep_offer(o, keywords, company.places)]
        new = self._offers.save(kept)
        closed = self._offers.close_missing(board.id_prefix, listing.open_ids, self._clock())
        return len(kept), new, closed

    def run_saved(self) -> SearchReport:
        """Every saved search and watched company. One failure never stops the rest."""
        runs = checked = found = new = closed = 0
        errors: list[str] = []
        if self._adzuna is not None:
            for saved in self._searches.list_searches():
                label = f"search {saved.search.what!r}"
                try:
                    result = self.search(saved.search)
                except AdzunaError as err:
                    errors.append(f"{label}: {err}")
                    continue
                runs += 1
                found, new = found + len(result.offers), new + result.new
        for company in self._searches.list_companies():
            try:
                kept, added, gone = self.check_company(company)
            except (BoardError, httpx.HTTPError, ValueError) as err:
                message = f"{company.name}: {type(err).__name__}: {err}"
                errors.append(message)
                self._searches.record_check(company.id, self._clock(), message)
                continue
            self._searches.record_check(company.id, self._clock(), None)
            checked += 1
            found, new, closed = found + kept, new + added, closed + gone
        for error in errors:
            logger.warning("job search failed", extra={"error": error})
        return SearchReport(runs, checked, found, new, closed, errors)


def build_job_searcher(
    settings: Settings,
    offers: OfferRepository,
    searches: SearchRepository,
    profile: CandidateProfile,
) -> JobSearcher:
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
    adzuna = None
    if settings.adzuna_app_id is not None and settings.adzuna_app_key is not None:
        adzuna = AdzunaSearch(
            make_adzuna_client(),
            app_id=settings.adzuna_app_id,
            app_key=settings.adzuna_app_key.get_secret_value(),
            clock=clock,
        )
    boards = CompanyBoards(make_boards_client(), clock=clock)
    return JobSearcher(offers, searches, profile, boards=boards, adzuna=adzuna, clock=clock)

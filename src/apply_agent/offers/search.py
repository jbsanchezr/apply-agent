"""The Search page's engine: title searches and watched companies (D62, D63).

A title search runs on every source set up for its country (today InfoJobs,
for Spain) and its results join the offers; one source failing leaves the
others' results. A saved search and every watched company run again on each
sync. Offers from here go through the same fit score and applied check as alert offers; they
only carry more, a description and, for company boards, closure for free.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from apply_agent.config import Settings
from apply_agent.domain.fit import found_words
from apply_agent.domain.offers import JobOffer
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.search import COUNTRY_NAMES, JobSearch, WatchedCompany
from apply_agent.offers.boards import Board, BoardError, BoardKind, BoardMatch, CompanyBoards
from apply_agent.offers.boards import make_client as make_boards_client
from apply_agent.offers.infojobs import InfoJobsError, InfoJobsSearch
from apply_agent.offers.infojobs import make_client as make_infojobs_client
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository

logger = logging.getLogger(__name__)


class SearchUnavailableError(RuntimeError):
    """No title source is set up for the search's country."""


@dataclass(frozen=True, slots=True)
class TitleSource:
    """A source that searches by title: a name, where it searches, how."""

    name: str
    run: Callable[[JobSearch], list[JobOffer]]
    # Countries it covers, or None for every country the form offers.
    countries: frozenset[str] | None = None

    @property
    def coverage(self) -> str:
        if self.countries is None:
            return "any country"
        return ", ".join(sorted(COUNTRY_NAMES[c] for c in self.countries))


@dataclass(frozen=True, slots=True)
class SearchResult:
    offers: tuple[JobOffer, ...]
    new: int
    sources: tuple[str, ...] = ()
    # One line per source that failed; the others' offers are still here.
    errors: tuple[str, ...] = ()


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
        infojobs: InfoJobsSearch | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._offers = offers
        self._searches = searches
        self._profile = profile
        self._boards = boards
        self._clock = clock
        self._sources: list[TitleSource] = []
        if infojobs is not None:
            self._sources.append(TitleSource("InfoJobs", infojobs.search, frozenset({"es"})))

    @property
    def title_sources(self) -> tuple[TitleSource, ...]:
        return tuple(self._sources)

    def sources_for(self, country: str) -> list[TitleSource]:
        return [s for s in self._sources if s.countries is None or country in s.countries]

    def search(self, search: JobSearch) -> SearchResult:
        """Run a title search on every source for its country; keep the results as offers."""
        sources = self.sources_for(search.country)
        if not sources:
            country = COUNTRY_NAMES[search.country]
            raise SearchUnavailableError(f"No title search is set up for {country}")
        found: dict[str, JobOffer] = {}
        errors: list[str] = []
        for source in sources:
            try:
                for offer in source.run(search):
                    found.setdefault(offer.key, offer)
            except InfoJobsError as err:
                errors.append(f"{source.name}: {err}")
        offers = tuple(found.values())
        names = tuple(s.name for s in sources)
        return SearchResult(offers, self._offers.save(offers), names, tuple(errors))

    def find_company(self, name: str) -> BoardMatch | None:
        return self._boards.find(name)

    def check_company(self, company: WatchedCompany) -> tuple[int, int, int]:
        """Read a watched company's board: (kept, new, closed). Raises on failure."""
        board = Board(BoardKind(company.board), company.slug)
        listing = self._boards.listing(board, company.name)
        keywords = company.keywords or self._profile.target
        kept = [o for o in listing.offers if keep_offer(o, keywords, company.places)]
        # Details cost a request each on some boards: only for postings not seen before.
        known = self._offers.known_keys([o.key for o in kept])
        fresh = self._boards.describe(board, [o for o in kept if o.key not in known])
        new = self._offers.save([*fresh, *(o for o in kept if o.key in known)])
        closed = 0
        if listing.complete:
            closed = self._offers.close_missing(board.id_prefix, listing.open_ids, self._clock())
        return len(kept), new, closed

    def watch_board(
        self,
        name: str,
        board: Board,
        keywords: Sequence[str] = (),
        places: Sequence[str] = (),
    ) -> tuple[int, int, int]:
        """Watch a company's board and read it once now: (company id, kept, new).

        A failed first read is recorded on the company and the next sync tries
        again; the company is watched either way.
        """
        company_id = self._searches.watch_company(
            name, board.kind.value, board.slug, keywords, places
        )
        watched = next(c for c in self._searches.list_companies() if c.id == company_id)
        try:
            kept, new, _ = self.check_company(watched)
        except (httpx.HTTPError, BoardError) as err:
            self._searches.record_check(company_id, self._clock(), f"{type(err).__name__}: {err}")
            return company_id, 0, 0
        self._searches.record_check(company_id, self._clock(), None)
        return company_id, kept, new

    def run_saved(self) -> SearchReport:
        """Every saved search and watched company. One failure never stops the rest."""
        runs = checked = found = new = closed = 0
        errors: list[str] = []
        for saved in self._searches.list_searches():
            label = f"search {saved.search.what!r}"
            try:
                result = self.search(saved.search)
            except SearchUnavailableError as err:
                errors.append(f"{label}: {err}")
                continue
            errors.extend(f"{label}: {error}" for error in result.errors)
            if len(result.errors) < len(result.sources):
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
    infojobs = None
    if settings.infojobs_client_id is not None and settings.infojobs_client_secret is not None:
        infojobs = InfoJobsSearch(
            make_infojobs_client(),
            client_id=settings.infojobs_client_id,
            client_secret=settings.infojobs_client_secret.get_secret_value(),
            clock=clock,
        )
    boards = CompanyBoards(make_boards_client(), clock=clock)
    return JobSearcher(offers, searches, profile, boards=boards, infojobs=infojobs, clock=clock)

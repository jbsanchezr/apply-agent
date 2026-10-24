"""Companies' own job boards, read through their public APIs (D62).

Greenhouse, Lever and Ashby host the careers pages of many companies and
publish every open posting as JSON, without a key, so that sites can list
them. That is what this reads: one request per company, each posting with
its full description. And because the listing is complete, a posting that
stops appearing has closed, which LinkedIn alerts can never tell (D61).

A company is found by its careers URL ("https://jobs.lever.co/acme") or by
trying slugs made from its name on each board in turn.
"""

import html
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from pydantic import ValidationError

from apply_agent.domain.keys import company_key
from apply_agent.domain.offers import MAX_DESCRIPTION, JobOffer, OfferSource
from apply_agent.offers.linkedin_search import USER_AGENT
from apply_agent.providers.html_text import html_to_text

logger = logging.getLogger(__name__)


class BoardKind(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    LEVER_EU = "lever_eu"
    ASHBY = "ashby"


_SOURCES: Final = {
    BoardKind.GREENHOUSE: OfferSource.GREENHOUSE,
    BoardKind.LEVER: OfferSource.LEVER,
    BoardKind.LEVER_EU: OfferSource.LEVER,
    BoardKind.ASHBY: OfferSource.ASHBY,
}
_URLS: Final = {
    BoardKind.GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    BoardKind.LEVER: "https://api.lever.co/v0/postings/{slug}?mode=json",
    BoardKind.LEVER_EU: "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    BoardKind.ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
# Careers pages whose address names the board and the company.
_CAREERS_URL: Final = (
    (BoardKind.GREENHOUSE, re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    (BoardKind.LEVER_EU, re.compile(r"jobs\.eu\.lever\.co/([\w-]+)")),
    (BoardKind.LEVER, re.compile(r"jobs\.lever\.co/([\w-]+)")),
    (BoardKind.ASHBY, re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)")),
)
_SLUG: Final = re.compile(r"^[\w.-]{1,100}$")


class BoardError(RuntimeError):
    """A board answered with something other than a listing."""


@dataclass(frozen=True, slots=True)
class Board:
    kind: BoardKind
    slug: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.slug):
            raise ValueError(f"not a board slug: {self.slug!r}")

    @property
    def source(self) -> OfferSource:
        return _SOURCES[self.kind]

    @property
    def id_prefix(self) -> str:
        """Every posting's external id from this board starts with this."""
        return f"{self.kind.value}/{self.slug}/"


@dataclass(frozen=True, slots=True)
class Listing:
    """Every open posting on a board, before any filter."""

    board: Board
    offers: tuple[JobOffer, ...]
    # External ids of all postings, including those that failed validation:
    # a posting listed but unreadable is still open.
    open_ids: frozenset[str]


def board_from_url(text: str) -> Board | None:
    """The board a careers URL points at, if it is one this module reads."""
    for kind, pattern in _CAREERS_URL:
        if match := pattern.search(text):
            return Board(kind, match.group(1))
    return None


def slug_candidates(company: str) -> list[str]:
    """'Picnic Technologies B.V.' -> picnictechnologies, picnic-technologies, picnic."""
    # The same normal form as an application's company: no case, accents or
    # legal suffix, so "Picnic Technologies B.V." gives "picnic technologies".
    words = company_key(company).split()
    if not words:
        return []
    candidates = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(c for c in candidates if _SLUG.match(c)))


def _date(value: Any) -> date | None:
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000, tz=UTC).date()
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, OverflowError):
        return None
    return None


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return html_to_text(value)[:MAX_DESCRIPTION] or None


def _https(url: Any) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") and len(url) <= 500 else None


def _greenhouse(data: Any) -> Iterator[dict[str, Any]]:
    for job in data.get("jobs", []):
        yield {
            "id": job.get("id"),
            "title": job.get("title"),
            "location": (job.get("location") or {}).get("name"),
            "url": job.get("absolute_url"),
            "posted_on": _date(job.get("first_published") or job.get("updated_at")),
            # Greenhouse escapes the HTML of its descriptions once more.
            "description": _text(html.unescape(job.get("content") or "")),
        }


def _lever(data: Any) -> Iterator[dict[str, Any]]:
    for job in data:
        lists = " ".join(
            f"{item.get('text', '')}\n{html_to_text(item.get('content') or '')}"
            for item in job.get("lists") or []
        )
        parts = (job.get("descriptionPlain"), lists, job.get("additionalPlain"))
        yield {
            "id": job.get("id"),
            "title": job.get("text"),
            "location": (job.get("categories") or {}).get("location"),
            "url": job.get("hostedUrl"),
            "posted_on": _date(job.get("createdAt")),
            "description": "\n".join(p for p in parts if p)[:MAX_DESCRIPTION] or None,
        }


def _ashby(data: Any) -> Iterator[dict[str, Any]]:
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        yield {
            "id": job.get("id"),
            "title": job.get("title"),
            "location": job.get("location"),
            "url": job.get("jobUrl"),
            "posted_on": _date(job.get("publishedAt")),
            "description": (job.get("descriptionPlain") or "")[:MAX_DESCRIPTION] or None,
        }


_READERS: Final[dict[BoardKind, Callable[[Any], Iterator[dict[str, Any]]]]] = {
    BoardKind.GREENHOUSE: _greenhouse,
    BoardKind.LEVER: _lever,
    BoardKind.LEVER_EU: _lever,
    BoardKind.ASHBY: _ashby,
}


class CompanyBoards:
    def __init__(self, client: httpx.Client, *, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    def _get(self, board: Board) -> Any | None:
        """The board's JSON, or None if the board does not have this company."""
        response = self._client.get(_URLS[board.kind].format(slug=board.slug))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as err:
            raise BoardError(f"{board.kind.value} answered with something other than JSON") from err

    def find(self, company: str) -> Board | None:
        """Where a company publishes its postings: a careers URL, or slugs tried in turn."""
        if board := board_from_url(company):
            return board if self._get(board) is not None else None
        for slug in slug_candidates(company):
            for kind in BoardKind:
                board = Board(kind, slug)
                data = self._get(board)
                # A Lever account with no postings is an empty list: often an
                # old account under a guessed name, so not taken as a match.
                empty_lever = kind in (BoardKind.LEVER, BoardKind.LEVER_EU) and not data
                if data is not None and not empty_lever:
                    return board
        return None

    def listing(self, board: Board, company: str) -> Listing:
        """Every open posting. Raises BoardError if the company is no longer there."""
        data = self._get(board)
        if data is None:
            raise BoardError(f"{company} is no longer on {board.kind.value} as {board.slug!r}")
        seen_at = self._clock()
        offers: list[JobOffer] = []
        open_ids: set[str] = set()
        for raw in _READERS[board.kind](data):
            if raw["id"] is None:
                continue
            external_id = f"{board.id_prefix}{raw['id']}"
            open_ids.add(external_id)
            try:
                offers.append(
                    JobOffer(
                        source=board.source,
                        external_id=external_id,
                        title=raw["title"] or "",
                        company=company,
                        location=(raw["location"] or None) and str(raw["location"])[:200],
                        url=_https(raw["url"]),
                        posted_on=raw["posted_on"],
                        description=raw["description"],
                        seen_at=seen_at,
                    )
                )
            except ValidationError:
                logger.info("skipped an unreadable posting", extra={"board": board.kind.value})
        return Listing(board, tuple(offers), frozenset(open_ids))


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)

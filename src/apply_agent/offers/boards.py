"""Companies' own job boards, read through their public APIs (D62, D63).

Greenhouse, Lever, Ashby, Recruitee, Personio, SmartRecruiters and Workday
host the careers pages of many companies and publish every open posting,
without a key, so that sites can list them. That is what this reads: one request per
company (a few for SmartRecruiters, which pages), each posting with its
description. And because the listing is complete, a posting that stops
appearing has closed, which LinkedIn alerts can never tell (D61).

A company is found by its careers URL ("https://jobs.lever.co/acme") or by
trying slugs made from its name on each board in turn. Workday only by its
URL ("https://acme.wd3.myworkdayjobs.com/External"): the address carries a
server and a site name that cannot be guessed (D71).
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final

import httpx
from pydantic import ValidationError

from apply_agent.domain.keys import company_key, similar_company
from apply_agent.domain.offers import MAX_DESCRIPTION, JobOffer, OfferSource
from apply_agent.offers.linkedin_search import USER_AGENT
from apply_agent.providers.html_text import html_to_text

logger = logging.getLogger(__name__)


class BoardKind(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    LEVER_EU = "lever_eu"
    ASHBY = "ashby"
    RECRUITEE = "recruitee"
    PERSONIO = "personio"
    PERSONIO_COM = "personio_com"
    SMARTRECRUITERS = "smartrecruiters"
    # Slug "tenant.wdN.site", from https://tenant.wdN.myworkdayjobs.com/site (D71).
    WORKDAY = "workday"


_SOURCES: Final = {
    BoardKind.GREENHOUSE: OfferSource.GREENHOUSE,
    BoardKind.LEVER: OfferSource.LEVER,
    BoardKind.LEVER_EU: OfferSource.LEVER,
    BoardKind.ASHBY: OfferSource.ASHBY,
    BoardKind.RECRUITEE: OfferSource.RECRUITEE,
    BoardKind.PERSONIO: OfferSource.PERSONIO,
    BoardKind.PERSONIO_COM: OfferSource.PERSONIO,
    BoardKind.SMARTRECRUITERS: OfferSource.SMARTRECRUITERS,
    BoardKind.WORKDAY: OfferSource.WORKDAY,
}
_URLS: Final = {
    BoardKind.GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    BoardKind.LEVER: "https://api.lever.co/v0/postings/{slug}?mode=json",
    BoardKind.LEVER_EU: "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    BoardKind.ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    BoardKind.RECRUITEE: "https://{slug}.recruitee.com/api/offers/",
    BoardKind.PERSONIO: "https://{slug}.jobs.personio.de/xml",
    BoardKind.PERSONIO_COM: "https://{slug}.jobs.personio.com/xml",
    BoardKind.SMARTRECRUITERS: "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    BoardKind.WORKDAY: "https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
}
_PERSONIO_HOSTS: Final = {
    BoardKind.PERSONIO: "jobs.personio.de",
    BoardKind.PERSONIO_COM: "jobs.personio.com",
}
# Careers pages whose address names the board and the company.
_CAREERS_URL: Final = (
    (BoardKind.GREENHOUSE, re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)")),
    (BoardKind.LEVER_EU, re.compile(r"jobs\.eu\.lever\.co/([\w-]+)")),
    (BoardKind.LEVER, re.compile(r"jobs\.lever\.co/([\w-]+)")),
    (BoardKind.ASHBY, re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)")),
    (BoardKind.RECRUITEE, re.compile(r"([\w-]+)\.recruitee\.com")),
    (BoardKind.PERSONIO, re.compile(r"([\w-]+)\.jobs\.personio\.de")),
    (BoardKind.PERSONIO_COM, re.compile(r"([\w-]+)\.jobs\.personio\.com")),
    (BoardKind.SMARTRECRUITERS, re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+)")),
    # Tenant, server and site; an optional locale ("en-US/") comes before the site.
    (
        BoardKind.WORKDAY,
        re.compile(r"([\w-]+)\.(wd\d{1,3})\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([\w-]+)"),
    ),
)
_SLUG: Final = re.compile(r"^[\w.-]{1,100}$")
_WORKDAY_SLUG: Final = re.compile(r"^[\w-]+\.wd\d{1,3}\.[\w-]+$")
# SmartRecruiters pages its listings. Big employers list thousands; past this
# many the listing is read in part, and closures are not inferred from it.
SMARTRECRUITERS_PAGE: Final = 100
SMARTRECRUITERS_MAX: Final = 1_000
# Workday serves 20 postings a page, newest first, and big employers list
# thousands: the newest this many are read, and closures are not inferred
# from a partial listing (D71).
WORKDAY_PAGE: Final = 20
WORKDAY_MAX: Final = 200
# Personio's yearsOfExperience codes, as the years a posting asks for.
_PERSONIO_YEARS: Final = {"1-2": "1-2", "2-5": "2-5", "5-7": "5-7", "7-10": "7-10", "10+": "10+"}


class BoardError(RuntimeError):
    """A board answered with something other than a listing."""


@dataclass(frozen=True, slots=True)
class Board:
    kind: BoardKind
    slug: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.slug):
            raise ValueError(f"not a board slug: {self.slug!r}")
        if self.kind is BoardKind.WORKDAY and not _WORKDAY_SLUG.match(self.slug):
            raise ValueError(f"not a Workday board: {self.slug!r}")

    @property
    def source(self) -> OfferSource:
        return _SOURCES[self.kind]

    @property
    def id_prefix(self) -> str:
        """Every posting's external id from this board starts with this."""
        return f"{self.kind.value}/{self.slug}/"

    @property
    def careers_url(self) -> str:
        """The public page where people browse this company's postings."""
        if self.kind is BoardKind.WORKDAY:
            tenant, server, site = self.slug.split(".")
            return f"https://{tenant}.{server}.myworkdayjobs.com/{site}"
        return _CAREERS_PAGES[self.kind].format(slug=self.slug)

    @property
    def lists_details(self) -> bool:
        """False when a posting's place, link and date are best read from its own page."""
        return self.kind not in (BoardKind.SMARTRECRUITERS, BoardKind.WORKDAY)

    @property
    def api_url(self) -> str:
        if self.kind is BoardKind.WORKDAY:
            tenant, server, site = self.slug.split(".")
            return _URLS[self.kind].format(tenant=tenant, server=server, site=site)
        return _URLS[self.kind].format(slug=self.slug)

    @property
    def company_name(self) -> str:
        """A name for a company known only by its URL: the slug, or Workday's tenant."""
        return self.slug.split(".")[0] if self.kind is BoardKind.WORKDAY else self.slug


_CAREERS_PAGES: Final = {
    BoardKind.GREENHOUSE: "https://job-boards.greenhouse.io/{slug}",
    BoardKind.LEVER: "https://jobs.lever.co/{slug}",
    BoardKind.LEVER_EU: "https://jobs.eu.lever.co/{slug}",
    BoardKind.ASHBY: "https://jobs.ashbyhq.com/{slug}",
    BoardKind.RECRUITEE: "https://{slug}.recruitee.com",
    BoardKind.PERSONIO: "https://{slug}.jobs.personio.de",
    BoardKind.PERSONIO_COM: "https://{slug}.jobs.personio.com",
    BoardKind.SMARTRECRUITERS: "https://careers.smartrecruiters.com/{slug}",
}


@dataclass(frozen=True, slots=True)
class BoardMatch:
    """A board that may hold a company's postings, and how sure that is (D66)."""

    board: Board
    # Real postings seen while probing (sample postings left out).
    postings: int
    # A few of their titles, for the user to judge an unconfirmed match.
    examples: tuple[str, ...]
    # Company names the board reports for its postings, if it reports any.
    names: frozenset[str]
    confirmed: bool


# Fewer real postings than this, under a guessed name, needs the user's word.
MIN_POSTINGS_TO_TRUST: Final = 5


def _same_name(asked: str, reported: str) -> bool:
    """'bosch group' and 'boschgroup', or 'bosch' and 'bosch group': one company."""
    if similar_company(asked, reported):
        return True
    a, b = asked.replace(" ", ""), reported.replace(" ", "")
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def _match(
    board: Board, rows: list[dict[str, Any]], company: str, *, confirmed: bool
) -> BoardMatch:
    real = [row for row in rows if not _is_sample(row)]
    names = frozenset(str(row["company"]) for row in real if row.get("company"))
    key = company_key(company)
    right_name = not names or any(_same_name(key, company_key(name)) for name in names)
    sure = confirmed or (len(real) >= MIN_POSTINGS_TO_TRUST and right_name)
    examples = tuple(str(row.get("title") or "") for row in real[:3])
    return BoardMatch(board, len(real), examples, names, sure)


@dataclass(frozen=True, slots=True)
class Listing:
    """The open postings on a board, before any filter."""

    board: Board
    offers: tuple[JobOffer, ...]
    # External ids of all postings, including those that failed validation:
    # a posting listed but unreadable is still open.
    open_ids: frozenset[str]
    # False when only part of the listing was read (SmartRecruiters' cap): a
    # posting missing from it may still be open, so nothing is closed.
    complete: bool = True


def board_from_url(text: str) -> Board | None:
    """The board a careers URL points at, if it is one this module reads."""
    for kind, pattern in _CAREERS_URL:
        if match := pattern.search(text):
            return Board(kind, ".".join(match.groups()))
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
            # Recruitee writes "2026-10-15 09:31:36 UTC".
            text = value.replace("Z", "+00:00").replace(" UTC", "+00:00")
            return datetime.fromisoformat(text).date()
    except (ValueError, OverflowError):
        return None
    return None


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return html_to_text(value)[:MAX_DESCRIPTION] or None


def _joined(parts: Iterable[str | None]) -> str | None:
    return "\n".join(p for p in parts if p)[:MAX_DESCRIPTION] or None


def _https(url: Any) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") and len(url) <= 500 else None


def _greenhouse(data: Any, board: Board) -> Iterator[dict[str, Any]]:
    for job in data.get("jobs", []):
        yield {
            "id": job.get("id"),
            "title": job.get("title"),
            "location": (job.get("location") or {}).get("name"),
            "url": job.get("absolute_url"),
            "posted_on": _date(job.get("first_published") or job.get("updated_at")),
            # Greenhouse escapes the HTML of its descriptions once more.
            "description": _text(html.unescape(job.get("content") or "")),
            "company": job.get("company_name"),
        }


def _lever(data: Any, board: Board) -> Iterator[dict[str, Any]]:
    for job in data:
        lists = " ".join(
            f"{item.get('text', '')}\n{html_to_text(item.get('content') or '')}"
            for item in job.get("lists") or []
        )
        yield {
            "id": job.get("id"),
            "title": job.get("text"),
            "location": (job.get("categories") or {}).get("location"),
            "url": job.get("hostedUrl"),
            "posted_on": _date(job.get("createdAt")),
            "description": _joined(
                (job.get("descriptionPlain"), lists, job.get("additionalPlain"))
            ),
        }


def _ashby(data: Any, board: Board) -> Iterator[dict[str, Any]]:
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


def _recruitee(data: Any, board: Board) -> Iterator[dict[str, Any]]:
    for job in data.get("offers", []):
        if job.get("status", "published") != "published":
            continue
        yield {
            "id": job.get("id"),
            "title": job.get("title"),
            "location": job.get("location") or job.get("city"),
            "url": job.get("careers_url"),
            "posted_on": _date(job.get("published_at") or job.get("created_at")),
            "description": _joined((_text(job.get("description")), _text(job.get("requirements")))),
            "company": job.get("company_name"),
        }


def _personio(root: ET.Element, board: Board) -> Iterator[dict[str, Any]]:
    host = _PERSONIO_HOSTS[board.kind]
    for position in root.iter("position"):
        job_id = position.findtext("id")
        offices = [position.findtext("office")] + [
            o.text for o in position.iterfind("additionalOffices/office")
        ]
        sections = [
            f"{d.findtext('name') or ''}\n{html_to_text(d.findtext('value') or '')}"
            for d in position.iterfind("jobDescriptions/jobDescription")
        ]
        # Personio states the years as a code; written out, years_required reads it.
        years = _PERSONIO_YEARS.get((position.findtext("yearsOfExperience") or "").strip())
        asked = f"Requires {years} years of experience." if years else None
        yield {
            "id": job_id,
            "title": position.findtext("name"),
            "location": ", ".join(o for o in offices if o) or None,
            "url": f"https://{board.slug}.{host}/job/{job_id}" if job_id else None,
            "posted_on": _date(position.findtext("createdAt")),
            "description": _joined([asked, *sections]),
            "company": position.findtext("subcompany"),
        }


def _smartrecruiters(content: Any, board: Board) -> Iterator[dict[str, Any]]:
    for job in content:
        location = job.get("location") or {}
        yield {
            "id": job.get("id"),
            "title": job.get("name"),
            "location": location.get("fullLocation") or location.get("city"),
            "url": f"https://jobs.smartrecruiters.com/{board.slug}/{job.get('id')}",
            "posted_on": _date(job.get("releasedDate")),
            # The listing has no description; describe() fetches it for the
            # postings worth keeping.
            "description": None,
            "company": (job.get("company") or {}).get("name"),
        }


_POSTED: Final = re.compile(r"posted (today|yesterday|(\d{1,3})\+? days? ago)", re.IGNORECASE)
_SEVERAL_PLACES: Final = re.compile(r"^\d+ locations?$", re.IGNORECASE)


def _posted_ago(text: Any, today: date) -> date | None:
    """Workday's "Posted 3 Days Ago" as a date; "30+ Days Ago" is no date."""
    match = _POSTED.search(text) if isinstance(text, str) else None
    if match is None or "+" in match.group(0):
        return None
    word = match.group(1).casefold()
    days = 0 if word == "today" else 1 if word == "yesterday" else int(match.group(2))
    return date.fromordinal(today.toordinal() - days)


def _workday(postings: Iterable[Any], board: Board, today: date) -> Iterator[dict[str, Any]]:
    for job in postings:
        path = job.get("externalPath")
        if not isinstance(path, str) or not path.startswith("/job/"):
            continue
        bullets = job.get("bulletFields") or []
        location = job.get("locationsText")
        yield {
            "id": (bullets[0] if bullets else None) or path,
            "title": job.get("title"),
            # "2 Locations": the details say which; describe() fills them in.
            "location": None if _SEVERAL_PLACES.match(location or "") else location,
            "url": board.careers_url + path,
            "posted_on": _posted_ago(job.get("postedOn"), today),
            "description": None,
        }


_READERS: Final[dict[BoardKind, Callable[[Any, Board], Iterator[dict[str, Any]]]]] = {
    BoardKind.GREENHOUSE: _greenhouse,
    BoardKind.LEVER: _lever,
    BoardKind.LEVER_EU: _lever,
    BoardKind.ASHBY: _ashby,
    BoardKind.RECRUITEE: _recruitee,
    BoardKind.PERSONIO: _personio,
    BoardKind.PERSONIO_COM: _personio,
    BoardKind.SMARTRECRUITERS: _smartrecruiters,
}


_SAMPLE: Final = re.compile(r"\(\s*sample\s*\)|\bsample (?:job|posting|vacancy)\b", re.IGNORECASE)


def _is_sample(row: dict[str, Any]) -> bool:
    """A placeholder posting a job board puts in new accounts (D66)."""
    return bool(_SAMPLE.search(str(row.get("title") or "")))


class CompanyBoards:
    def __init__(self, client: httpx.Client, *, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    def _get(self, url: str, **params: Any) -> httpx.Response | None:
        """The response, or None if the board does not have this company."""
        response = self._client.get(url, params=params or None)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response

    def _json(self, board: Board, url: str, **params: Any) -> Any | None:
        response = self._get(url, **params)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as err:
            raise BoardError(f"{board.kind.value} answered with something other than JSON") from err

    def _fetch(
        self, board: Board, *, probe: bool = False
    ) -> tuple[list[dict[str, Any]], bool] | None:
        """Raw postings and whether that is all of them, or None if the company is not there."""
        url = board.api_url
        if board.kind is BoardKind.WORKDAY:
            return self._workday(board, url, probe=probe)
        reader = _READERS[board.kind]
        if board.kind in (BoardKind.PERSONIO, BoardKind.PERSONIO_COM):
            response = self._get(url)
            if response is None:
                return None
            try:
                # A public board's own feed. Python's expat refuses entity
                # expansion attacks (billion laughs) since 3.7.1.
                root = ET.fromstring(response.content)  # noqa: S314
            except ET.ParseError as err:
                raise BoardError("personio answered with something other than XML") from err
            if root.tag != "workzag-jobs":
                return None
            return list(reader(root, board)), True
        if board.kind is BoardKind.SMARTRECRUITERS:
            return self._smartrecruiters(board, url, probe=probe)
        data = self._json(board, url)
        return None if data is None else (list(reader(data, board)), True)

    def _smartrecruiters(
        self, board: Board, url: str, *, probe: bool
    ) -> tuple[list[dict[str, Any]], bool] | None:
        """Pages of postings; with ``probe``, one page is enough to know the company is there."""
        content: list[Any] = []
        total = 0
        while len(content) < SMARTRECRUITERS_MAX:
            page = self._json(board, url, limit=SMARTRECRUITERS_PAGE, offset=len(content))
            if page is None:
                return None
            total = int(page.get("totalFound") or 0)
            rows = page.get("content") or []
            content.extend(rows)
            if probe or not rows or len(content) >= total:
                break
        if total == 0:
            # It answers any name with an empty listing, so empty means not here.
            return None
        return list(_smartrecruiters(content, board)), len(content) >= total

    def _workday(
        self, board: Board, url: str, *, probe: bool
    ) -> tuple[list[dict[str, Any]], bool] | None:
        """Pages of the newest postings; only the first page says how many there are."""
        postings: list[Any] = []
        total = 0
        while len(postings) < WORKDAY_MAX:
            body = {"limit": WORKDAY_PAGE, "offset": len(postings), "searchText": ""}
            response = self._client.post(url, json={**body, "appliedFacets": {}})
            # A wrong site answers 404 or 422; an unknown tenant, 404 or a 5xx.
            if response.status_code in (404, 422) or (response.status_code >= 500 and not postings):
                return None
            response.raise_for_status()
            try:
                page = response.json()
            except ValueError as err:
                raise BoardError("workday answered with something other than JSON") from err
            if not postings:
                total = int(page.get("total") or 0)
            rows = page.get("jobPostings") or []
            postings.extend(rows)
            if probe or not rows or len(postings) >= total:
                break
        if total == 0:
            return None
        return list(_workday(postings, board, self._clock().date())), len(postings) >= total

    def find(self, company: str) -> BoardMatch | None:
        """Where a company publishes its postings: a careers URL, or slugs tried in turn.

        A careers URL is the user's word, so its board is confirmed. A board
        found by guessing slugs is confirmed only with enough real postings
        and, where the board names its company, the right name (D66): short
        names are taken by other companies ("meta", "amazon") and by trial
        accounts. Otherwise the match is returned unconfirmed, for the user
        to check, and the first one found wins.
        """
        if board := board_from_url(company):
            fetched = self._fetch(board, probe=True)
            return None if fetched is None else _match(board, fetched[0], company, confirmed=True)
        possible: BoardMatch | None = None
        for slug in slug_candidates(company):
            for kind in BoardKind:
                if kind is BoardKind.WORKDAY:
                    continue  # found only by its URL
                board = Board(kind, slug)
                try:
                    fetched = self._fetch(board, probe=True)
                except (httpx.HTTPError, BoardError) as err:
                    # One board failing (a 403, a timeout) must not end the search.
                    logger.info(
                        "board probe failed",
                        extra={"board": kind.value, "error": type(err).__name__},
                    )
                    continue
                if fetched is None:
                    continue
                match = _match(board, fetched[0], company, confirmed=False)
                if match.confirmed:
                    return match
                # An empty account, or one with only the board's own sample
                # postings ("Senior Marketer (Sample)"), is not worth showing.
                if possible is None and match.postings:
                    possible = match
        return possible

    def listing(self, board: Board, company: str) -> Listing:
        """The open postings. Raises BoardError if the company is no longer there."""
        fetched = self._fetch(board)
        if fetched is None:
            raise BoardError(f"{company} is no longer on {board.kind.value} as {board.slug!r}")
        rows, complete = fetched
        seen_at = self._clock()
        offers: list[JobOffer] = []
        open_ids: set[str] = set()
        for raw in rows:
            if raw["id"] is None or _is_sample(raw):
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
        return Listing(board, tuple(offers), frozenset(open_ids), complete)

    def describe(self, board: Board, offers: Iterable[JobOffer]) -> list[JobOffer]:
        """Fill in descriptions a listing lacks: one request per SmartRecruiters posting.

        Other boards list their descriptions, so their offers come back as they
        are. A posting whose details fail keeps no description, not the sync.
        """
        if board.kind is BoardKind.WORKDAY:
            return [self._workday_details(board, offer) for offer in offers]
        if board.kind is not BoardKind.SMARTRECRUITERS:
            return list(offers)
        base = board.api_url
        described = []
        for offer in offers:
            posting_id = (offer.external_id or "").removeprefix(board.id_prefix)
            try:
                data = self._json(board, f"{base}/{posting_id}")
            except (httpx.HTTPError, BoardError):
                logger.info("skipped a posting's details", extra={"board": board.kind.value})
                data = None
            if not data:
                described.append(offer)
                continue
            sections = ((data.get("jobAd") or {}).get("sections")) or {}
            text = _joined(
                _text((sections.get(name) or {}).get("text"))
                for name in ("jobDescription", "qualifications", "additionalInformation")
            )
            url = _https(data.get("postingUrl")) or offer.url
            described.append(offer.model_copy(update={"description": text, "url": url}))
        return described

    def _workday_details(self, board: Board, offer: JobOffer) -> JobOffer:
        """A Workday posting's description, places and exact date, from its own page."""
        path = (offer.url or "").removeprefix(board.careers_url)
        if not path.startswith("/job/"):
            return offer
        try:
            data = self._json(board, board.api_url.removesuffix("/jobs") + path)
        except (httpx.HTTPError, BoardError):
            logger.info("skipped a posting's details", extra={"board": board.kind.value})
            return offer
        info = (data or {}).get("jobPostingInfo") or {}
        if not info:
            return offer
        country = (info.get("country") or {}).get("descriptor")
        places = [info.get("location"), *(info.get("additionalLocations") or [])]
        location = "; ".join(str(p) for p in places if p) or offer.location
        if location and country and country not in location:
            location = f"{location}, {country}"
        return offer.model_copy(
            update={
                "description": _text(info.get("jobDescription")),
                "location": location and location[:200],
                "posted_on": _date(info.get("startDate")) or offer.posted_on,
                "url": _https(info.get("externalUrl")) or offer.url,
            }
        )


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)

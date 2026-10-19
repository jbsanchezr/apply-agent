"""Search LinkedIn's public job listings: the pages it shows when signed out.

Off by default (see D57). No account is used, so the user's LinkedIn account
is never at risk, but this is still scraping: LinkedIn's terms forbid it, it
may answer with 429 or 999 at any time, and its HTML can change without
notice. So requests are few (a couple of pages per search), slow (a pause
between them), and a refusal stops the scan instead of retrying.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Final

import httpx
from pydantic import ValidationError

from apply_agent.config import LinkedInSearch
from apply_agent.domain.offers import JobOffer, OfferSource
from apply_agent.offers.alerts import posting_url

logger = logging.getLogger(__name__)

SEARCH_URL: Final = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE: Final = 10
PAUSE_SECONDS: Final = 3.0
# Says what this is instead of posing as a browser.
USER_AGENT: Final = (
    "apply-agent/0.1 (personal job search; +https://github.com/jbsanchezr/apply-agent)"
)
# LinkedIn answers 999 when it refuses a client it thinks is automated.
_REFUSED: Final = frozenset({403, 429, 999})
_JOB_URN: Final = re.compile(r"urn:li:jobPosting:(\d{1,20})")
_FIELDS: Final = {
    "base-search-card__title": "title",
    "base-search-card__subtitle": "company",
    "job-search-card__location": "location",
}


class SearchRefusedError(RuntimeError):
    """LinkedIn refused the request; further requests now would be refused too."""


class _ResultsParser(HTMLParser):
    """Collects one dict per job card from a page of search results."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, str]] = []
        self._field: str | None = None
        self._tag = ""
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        urn = _JOB_URN.search(attributes.get("data-entity-urn", ""))
        if urn is not None:
            self.cards.append({"id": urn.group(1)})
        if not self.cards:
            return
        if self._field is not None:
            if tag == self._tag:
                self._depth += 1
            return
        if tag == "time" and attributes.get("datetime"):
            self.cards[-1].setdefault("posted_on", attributes["datetime"])
        for css, field in _FIELDS.items():
            if css in attributes.get("class", "").split():
                self._field, self._tag, self._depth, self._text = field, tag, 0, []

    def handle_endtag(self, tag: str) -> None:
        if self._field is None or tag != self._tag:
            return
        if self._depth:
            self._depth -= 1
            return
        self.cards[-1].setdefault(self._field, " ".join("".join(self._text).split()))
        self._field = None

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._text.append(data)


def _posted_on(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_search_results(html: str, seen_at: datetime) -> list[JobOffer]:
    """Offers on one page of results. Cards without a title or company are skipped."""
    parser = _ResultsParser()
    parser.feed(html)
    parser.close()
    offers = []
    for card in parser.cards:
        try:
            offers.append(
                JobOffer(
                    source=OfferSource.LINKEDIN_SEARCH,
                    linkedin_id=card["id"],
                    title=card.get("title", ""),
                    company=card.get("company", ""),
                    location=card.get("location") or None,
                    url=posting_url(card["id"]),
                    posted_on=_posted_on(card.get("posted_on")),
                    seen_at=seen_at,
                )
            )
        except ValidationError:
            continue
    return offers


class LinkedInGuestSearch:
    def __init__(
        self,
        client: httpx.Client,
        *,
        pages: int,
        posted_within_days: int,
        clock: Callable[[], datetime],
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._pages = pages
        self._posted_within = posted_within_days
        self._clock = clock
        self._pause = pause
        self._requests = 0

    def search(self, query: LinkedInSearch) -> list[JobOffer]:
        """Up to ``pages`` pages of postings from the last ``posted_within_days`` days.

        Raises ``SearchRefusedError`` if LinkedIn refuses, and ``httpx.HTTPError``
        for any other failure.
        """
        offers: list[JobOffer] = []
        for page in range(self._pages):
            if self._requests:
                self._pause(PAUSE_SECONDS)
            self._requests += 1
            response = self._client.get(
                SEARCH_URL,
                params={
                    "keywords": query.keywords,
                    "location": query.location,
                    "f_TPR": f"r{self._posted_within * 86_400}",
                    "start": page * PAGE_SIZE,
                },
            )
            if response.status_code in _REFUSED:
                raise SearchRefusedError(
                    f"LinkedIn refused the search (HTTP {response.status_code}); "
                    "it blocks clients it takes for bots"
                )
            response.raise_for_status()
            found = parse_search_results(response.text, self._clock())
            offers.extend(found)
            if len(found) < PAGE_SIZE:
                break
        logger.info("linkedin search finished", extra={"offers": len(offers)})
        return offers


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
        timeout=20.0,
        follow_redirects=False,
    )

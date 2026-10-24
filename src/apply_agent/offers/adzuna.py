"""Search Adzuna's job API by title, place, country and company (D62).

Adzuna aggregates job boards and sells API access: an app id and key from
developer.adzuna.com (25 test calls, then a paid plan), sent as query
parameters. It returns a
title, company, place, date and a snippet of the description, enough for the
fit score to read "3+ years" when the snippet says it. Its terms ask for
attribution, which the Search page shows.

The key travels in the URL, so the httpx request log is silenced (see
``configure_logging``) and errors never repeat the URL.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

import httpx
from pydantic import ValidationError

from apply_agent.domain.offers import MAX_DESCRIPTION, JobOffer, OfferSource
from apply_agent.domain.search import JobSearch
from apply_agent.offers.linkedin_search import USER_AGENT
from apply_agent.providers.html_text import html_to_text

logger = logging.getLogger(__name__)

SEARCH_URL: Final = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
RESULTS_PER_PAGE: Final = 50


class AdzunaError(RuntimeError):
    """Adzuna refused or failed the search. The message never holds the key."""


def _text(value: Any, limit: int) -> str:
    return html_to_text(value)[:limit] if isinstance(value, str) else ""


def _date(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


class AdzunaSearch:
    def __init__(
        self,
        client: httpx.Client,
        *,
        app_id: str,
        app_key: str,
        clock: Callable[[], datetime],
        pages: int = 1,
    ) -> None:
        self._client = client
        self._credentials = {"app_id": app_id, "app_key": app_key}
        self._clock = clock
        self._pages = pages

    def _page(self, search: JobSearch, page: int) -> list[Any]:
        params: dict[str, str | int] = {
            **self._credentials,
            "results_per_page": RESULTS_PER_PAGE,
            "what": search.what,
            "max_days_old": search.max_days_old,
            "sort_by": "date",
            "content-type": "application/json",
        }
        if search.where:
            params["where"] = search.where
        if search.company:
            params["company"] = search.company
        url = SEARCH_URL.format(country=search.country, page=page)
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as err:
            raise AdzunaError(f"Adzuna could not be reached ({type(err).__name__})") from None
        if response.status_code in (401, 403):
            raise AdzunaError("Adzuna refused the app id or key")
        if response.status_code >= 400:
            raise AdzunaError(f"Adzuna answered {response.status_code}")
        try:
            results = response.json().get("results", [])
        except ValueError:
            raise AdzunaError("Adzuna answered with something other than JSON") from None
        return results if isinstance(results, list) else []

    def search(self, search: JobSearch) -> list[JobOffer]:
        """The offers matching the search, newest first. Raises AdzunaError."""
        seen_at = self._clock()
        offers: list[JobOffer] = []
        for page in range(1, self._pages + 1):
            results = self._page(search, page)
            for job in results:
                link = job.get("redirect_url")
                url = link if isinstance(link, str) and link.startswith("https://") else None
                try:
                    offers.append(
                        JobOffer(
                            source=OfferSource.ADZUNA,
                            external_id=f"adzuna/{search.country}/{job.get('id')}",
                            title=_text(job.get("title"), 200),
                            company=_text((job.get("company") or {}).get("display_name"), 200)
                            or "Unknown company",
                            location=_text((job.get("location") or {}).get("display_name"), 200)
                            or None,
                            url=url
                            if isinstance(url, str) and url.startswith("https://")
                            else None,
                            posted_on=_date(job.get("created")),
                            description=_text(job.get("description"), MAX_DESCRIPTION) or None,
                            seen_at=seen_at,
                        )
                    )
                except ValidationError:
                    logger.info("skipped an unreadable Adzuna result")
            if len(results) < RESULTS_PER_PAGE:
                break
        return offers


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0)

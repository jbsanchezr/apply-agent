"""Search InfoJobs, Spain's largest job board, through its official API (D63).

The offer search is a public operation: an app registered with an InfoJobs
account at developer.infojobs.net gets a client id and secret, sent as HTTP
basic auth. Each offer states its minimum experience as a value from
InfoJobs' own list ("Al menos 2 años", "Más de 5 años", "No Requerida") and
its minimum requirements as text; both go into the description, where
``years_required`` reads them.

Not checked against the live API: that needs the user's credentials. It
follows the documented operation (offer list, version 9). The secret never
appears in errors or logs.
"""

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

import httpx
from pydantic import ValidationError

from apply_agent.domain.keys import company_key, role_key, similar_company
from apply_agent.domain.offers import MAX_DESCRIPTION, JobOffer, OfferSource
from apply_agent.domain.search import JobSearch
from apply_agent.offers.linkedin_search import USER_AGENT

logger = logging.getLogger(__name__)

SEARCH_URL: Final = "https://api.infojobs.net/api/9/offer"
MAX_RESULTS: Final = 50
# InfoJobs' sinceDate takes these windows only; a search asks for the
# narrowest that still covers its age.
_SINCE: Final = ((1, "_24_HOURS"), (7, "_7_DAYS"), (15, "_15_DAYS"))


class InfoJobsError(RuntimeError):
    """InfoJobs refused or failed the search. The message never holds the secret."""


def since_date(max_days_old: int) -> str:
    return next((code for days, code in _SINCE if max_days_old <= days), "ANY")


def province_key(place: str) -> str:
    """'Santa Cruz de Tenerife' -> 'santa-cruz-de-tenerife', InfoJobs' province keys."""
    return "-".join(role_key(place).split())


def _date(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _value(item: Any, name: str) -> str | None:
    field = item.get(name)
    value = field.get("value") if isinstance(field, dict) else field
    return value if isinstance(value, str) and value.strip() else None


def _description(item: Any) -> str | None:
    experience = _value(item, "experienceMin")
    parts = [
        f"Experiencia mínima: {experience}." if experience else None,
        item.get("requirementMin") if isinstance(item.get("requirementMin"), str) else None,
    ]
    return "\n".join(p for p in parts if p)[:MAX_DESCRIPTION] or None


class InfoJobsSearch:
    def __init__(
        self,
        client: httpx.Client,
        *,
        client_id: str,
        client_secret: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._auth = httpx.BasicAuth(client_id, client_secret)
        self._clock = clock

    def _items(self, search: JobSearch) -> list[Any]:
        params: dict[str, str | int] = {
            "q": search.what,
            "sinceDate": since_date(search.max_days_old),
            "order": "updated-desc",
            "maxResults": MAX_RESULTS,
        }
        if search.where:
            params["province"] = province_key(search.where)
        try:
            response = self._client.get(SEARCH_URL, params=params, auth=self._auth)
        except httpx.HTTPError as err:
            raise InfoJobsError(f"InfoJobs could not be reached ({type(err).__name__})") from None
        if response.status_code == 401:
            raise InfoJobsError("InfoJobs refused the client id or secret")
        if response.status_code >= 400:
            code = ""
            with contextlib.suppress(ValueError):
                code = str(response.json().get("error", ""))
            if code == "301":
                raise InfoJobsError(f"InfoJobs does not know the province {search.where!r}")
            raise InfoJobsError(f"InfoJobs answered {response.status_code}")
        try:
            items = response.json().get("items", [])
        except ValueError:
            raise InfoJobsError("InfoJobs answered with something other than JSON") from None
        return items if isinstance(items, list) else []

    def search(self, search: JobSearch) -> list[JobOffer]:
        """Offers in Spain matching the search, newest first. Raises InfoJobsError.

        InfoJobs cannot filter by company name, so a company is matched here,
        with the same tolerance as applications (D4).
        """
        seen_at = self._clock()
        wanted = company_key(search.company) if search.company else None
        offers: list[JobOffer] = []
        for item in self._items(search):
            author = _value(item, "author") or (item.get("author") or {}).get("name")
            if wanted and not (author and similar_company(wanted, company_key(author))):
                continue
            link = item.get("link")
            place = ", ".join(p for p in (item.get("city"), _value(item, "province")) if p)
            try:
                offers.append(
                    JobOffer(
                        source=OfferSource.INFOJOBS,
                        external_id=f"infojobs/{item.get('id')}",
                        title=str(item.get("title") or "")[:200],
                        company=str(author or "Unknown company")[:200],
                        location=place[:200] or None,
                        url=link if isinstance(link, str) and link.startswith("https://") else None,
                        posted_on=_date(item.get("published")),
                        description=_description(item),
                        seen_at=seen_at,
                    )
                )
            except ValidationError:
                logger.info("skipped an unreadable InfoJobs offer")
        return offers


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0)

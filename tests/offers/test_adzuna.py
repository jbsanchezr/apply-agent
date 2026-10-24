"""Adzuna's search API, served by a fake transport in its documented shape.

Not yet checked against the live API: that needs the user's key. Every
company and text below is invented.
"""

import json
from datetime import date

import httpx
import pytest

from apply_agent.domain.offers import OfferSource
from apply_agent.domain.search import JobSearch
from apply_agent.offers.adzuna import RESULTS_PER_PAGE, AdzunaError, AdzunaSearch
from tests.agent.helpers import T0

KEY = "secret-key-not-real"


def result(n: int) -> dict[str, object]:
    return {
        "id": str(5000 + n),
        "title": "<strong>AI</strong> Engineer",
        "company": {"display_name": "Arcwell"},
        "location": {"display_name": "Utrecht, Utrecht"},
        "created": "2026-10-21T08:00:00Z",
        "redirect_url": f"https://www.adzuna.nl/details/{5000 + n}",
        "description": "We ask for 2+ years of experience with LLMs &amp; Python...",
    }


def adzuna(handler: httpx.MockTransport, pages: int = 1) -> AdzunaSearch:
    return AdzunaSearch(
        httpx.Client(transport=handler), app_id="app", app_key=KEY, clock=lambda: T0, pages=pages
    )


def test_a_search_sends_its_fields_and_reads_the_results() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=json.dumps({"results": [result(1)], "count": 1}))

    search = JobSearch(what="AI engineer", where="Utrecht", country="nl", company="Arcwell")
    [offer] = adzuna(httpx.MockTransport(handle)).search(search)

    url = requests[0].url
    assert url.path == "/v1/api/jobs/nl/search/1"
    assert url.params["what"] == "AI engineer"
    assert url.params["where"] == "Utrecht"
    assert url.params["company"] == "Arcwell"
    assert url.params["max_days_old"] == "14"
    assert url.params["app_key"] == KEY
    assert (offer.source, offer.title, offer.company, offer.location) == (
        OfferSource.ADZUNA,
        "AI Engineer",
        "Arcwell",
        "Utrecht, Utrecht",
    )
    assert offer.posted_on == date(2026, 10, 21)
    assert offer.description == "We ask for 2+ years of experience with LLMs & Python..."
    assert offer.external_id == "adzuna/nl/5001"


def test_pages_are_read_until_a_short_one() -> None:
    pages: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        page = request.url.path.rsplit("/", 1)[1]
        pages.append(page)
        count = RESULTS_PER_PAGE if page == "1" else 3
        return httpx.Response(200, text=json.dumps({"results": [result(n) for n in range(count)]}))

    found = adzuna(httpx.MockTransport(handle), pages=5).search(JobSearch(what="python"))
    assert pages == ["1", "2"]
    assert len(found) == RESULTS_PER_PAGE + 3


@pytest.mark.parametrize(("code", "message"), [(401, "refused"), (503, "answered 503")])
def test_errors_never_show_the_key(code: int, message: str) -> None:
    handler = httpx.MockTransport(lambda _: httpx.Response(code, text="{}"))
    with pytest.raises(AdzunaError, match=message) as caught:
        adzuna(handler).search(JobSearch(what="python"))
    assert KEY not in str(caught.value)

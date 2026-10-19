from datetime import date

import httpx
import pytest

from apply_agent.config import LinkedInSearch
from apply_agent.domain.offers import OfferSource
from apply_agent.offers.linkedin_search import (
    PAGE_SIZE,
    SEARCH_URL,
    LinkedInGuestSearch,
    SearchRefusedError,
    parse_search_results,
)
from tests.agent.helpers import T0
from tests.conftest import FIXTURES_DIR

PAGE = (FIXTURES_DIR / "offers" / "linkedin-search.html").read_text(encoding="utf-8")
QUERY = LinkedInSearch(keywords="data engineer", location="Madrid")


def _card(n: int) -> str:
    return (
        f'<div data-entity-urn="urn:li:jobPosting:{n}">'
        f'<h3 class="base-search-card__title">Role {n}</h3>'
        f'<h4 class="base-search-card__subtitle">Company {n}</h4></div>'
    )


def test_parses_cards_and_skips_incomplete_ones() -> None:
    offers = parse_search_results(PAGE, T0)
    assert [(o.linkedin_id, o.title, o.company, o.location, o.posted_on) for o in offers] == [
        (
            "4100000001",
            "Data Engineer",
            "Arcwell & Co",
            "Madrid, Comunidad de Madrid, España",
            date(2026, 10, 15),
        ),
        ("4100000002", "Senior ML Engineer", "Brisa Mobility", "España", None),
    ]
    assert offers[0].url == "https://www.linkedin.com/jobs/view/4100000001/"
    assert all(o.source is OfferSource.LINKEDIN_SEARCH for o in offers)


def _search(handler: httpx.MockTransport, pauses: list[float], pages: int = 3) -> list[str]:
    client = httpx.Client(transport=handler)
    search = LinkedInGuestSearch(
        client, pages=pages, posted_within_days=7, clock=lambda: T0, pause=pauses.append
    )
    return [o.title for o in search.search(QUERY)]


def test_pages_until_a_short_page_and_pauses_between_requests() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        start = int(request.url.params["start"])
        count = PAGE_SIZE if start == 0 else 2
        return httpx.Response(200, text="".join(_card(start + i + 1) for i in range(count)))

    pauses: list[float] = []
    titles = _search(httpx.MockTransport(handle), pauses)

    assert len(titles) == PAGE_SIZE + 2
    assert [r.url.params["start"] for r in requests] == ["0", "10"]
    first = requests[0].url
    assert str(first).startswith(SEARCH_URL)
    assert first.params["keywords"] == "data engineer"
    assert first.params["location"] == "Madrid"
    assert first.params["f_TPR"] == "r604800"
    assert len(pauses) == 1


@pytest.mark.parametrize("code", [429, 999])
def test_a_refusal_stops_the_search(code: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(code, text="<script></script>"))
    with pytest.raises(SearchRefusedError, match=str(code)):
        _search(transport, [])


def test_other_errors_are_http_errors() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        _search(transport, [])

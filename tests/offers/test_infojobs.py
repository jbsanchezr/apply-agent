"""InfoJobs' offer search, served by a fake transport in its documented shape.

Not yet checked against the live API: that needs the user's credentials.
Every company and text below is invented.
"""

import base64
import json
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from apply_agent.domain.offers import OfferSource
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.requirements import years_required
from apply_agent.domain.search import JobSearch
from apply_agent.offers.adzuna import AdzunaSearch
from apply_agent.offers.boards import CompanyBoards
from apply_agent.offers.infojobs import (
    InfoJobsError,
    InfoJobsSearch,
    province_key,
    since_date,
)
from apply_agent.offers.search import JobSearcher, SearchUnavailableError
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository
from tests.agent.helpers import T0
from tests.offers.test_adzuna import result as adzuna_result

SECRET = "secret-not-real"  # noqa: S105 - fake secret for tests


def item(
    n: int, author: str = "Nubaria Tech S.L.", experience: str = "Al menos 2 años"
) -> dict[str, Any]:
    return {
        "id": f"abc{n}",
        "title": "Desarrollador/a Backend Python",
        "author": {"name": author},
        "city": "Madrid",
        "province": {"id": 33, "value": "Madrid"},
        "link": f"https://www.infojobs.net/madrid/desarrollador-backend/of-iabc{n}",
        "published": "2026-10-21T08:00:00.000Z",
        "experienceMin": {"id": 3, "value": experience},
        "requirementMin": "Python, Django y experiencia con APIs REST.",
    }


def infojobs(handler: httpx.MockTransport) -> InfoJobsSearch:
    return InfoJobsSearch(
        httpx.Client(transport=handler), client_id="app", client_secret=SECRET, clock=lambda: T0
    )


def serve(
    *items: dict[str, Any], status: int = 200, body: dict[str, Any] | None = None
) -> httpx.MockTransport:
    payload = body if body is not None else {"items": list(items), "totalResults": len(items)}
    return httpx.MockTransport(lambda _: httpx.Response(status, text=json.dumps(payload)))


@pytest.mark.parametrize(
    ("days", "code"), [(1, "_24_HOURS"), (5, "_7_DAYS"), (14, "_15_DAYS"), (30, "ANY")]
)
def test_the_age_maps_to_the_narrowest_window_that_covers_it(days: int, code: str) -> None:
    assert since_date(days) == code


def test_places_become_province_keys() -> None:
    assert province_key("Madrid") == "madrid"
    assert province_key("Santa Cruz de Tenerife") == "santa-cruz-de-tenerife"
    assert province_key("A Coruña") == "a-coruna"


def test_a_search_sends_its_fields_and_reads_the_offers() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=json.dumps({"items": [item(1)]}))

    search = JobSearch(what="python", where="Madrid", country="es", max_days_old=7)
    [offer] = infojobs(httpx.MockTransport(handle)).search(search)

    url = requests[0].url
    assert url.path == "/api/9/offer"
    assert (url.params["q"], url.params["province"], url.params["sinceDate"]) == (
        "python",
        "madrid",
        "_7_DAYS",
    )
    expected = base64.b64encode(f"app:{SECRET}".encode()).decode()
    assert requests[0].headers["authorization"] == f"Basic {expected}"
    assert (offer.source, offer.company, offer.location) == (
        OfferSource.INFOJOBS,
        "Nubaria Tech S.L.",
        "Madrid, Madrid",
    )
    assert offer.posted_on == date(2026, 10, 21)
    assert offer.external_id == "infojobs/abc1"


@pytest.mark.parametrize(
    ("experience", "years"),
    [
        ("Al menos 1 año", 1),
        ("Al menos 2 años", 2),
        ("Más de 5 años", 5),
        ("Más de 10 años", 10),
        ("No Requerida", None),
    ],
)
def test_the_minimum_experience_is_read_as_years(experience: str, years: int | None) -> None:
    [offer] = infojobs(serve(item(1, experience=experience))).search(
        JobSearch(what="python", country="es")
    )
    assert years_required(offer.description) == years


def test_a_company_is_matched_by_name_since_infojobs_cannot_filter_by_it() -> None:
    found = infojobs(serve(item(1), item(2, author="Otra Empresa SA"))).search(
        JobSearch(what="python", country="es", company="Nubaria Tech")
    )
    assert [o.external_id for o in found] == ["infojobs/abc1"]


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": "102", "error_description": "Client credentials not valid"}, "refused"),
        (400, {"error": "301"}, "does not know the province 'Narnia'"),
        (503, {}, "answered 503"),
    ],
)
def test_errors_never_show_the_secret(status: int, body: dict[str, Any], message: str) -> None:
    with pytest.raises(InfoJobsError, match=message) as caught:
        infojobs(serve(status=status, body=body)).search(
            JobSearch(what="python", where="Narnia", country="es")
        )
    assert SECRET not in str(caught.value)


def searcher(engine: Engine, *, adzuna: bool, infojobs_status: int = 200) -> JobSearcher:
    def handle(request: httpx.Request) -> httpx.Response:
        if "adzuna" in str(request.url):
            return httpx.Response(200, text=json.dumps({"results": [adzuna_result(1)]}))
        if infojobs_status != 200:
            return httpx.Response(infojobs_status, text="{}")
        return httpx.Response(200, text=json.dumps({"items": [item(1)]}))

    client = httpx.Client(transport=httpx.MockTransport(handle))
    sessions = make_session_factory(engine)
    return JobSearcher(
        OfferRepository(sessions),
        SearchRepository(sessions),
        CandidateProfile(),
        boards=CompanyBoards(client, clock=lambda: T0),
        adzuna=AdzunaSearch(client, app_id="a", app_key="k", clock=lambda: T0) if adzuna else None,
        infojobs=InfoJobsSearch(client, client_id="i", client_secret=SECRET, clock=lambda: T0),
        clock=lambda: T0,
    )


def test_spain_is_searched_on_every_source_that_covers_it(engine: Engine) -> None:
    result = searcher(engine, adzuna=True).search(JobSearch(what="python", country="es"))
    assert result.sources == ("Adzuna", "InfoJobs")
    assert {o.source for o in result.offers} == {OfferSource.ADZUNA, OfferSource.INFOJOBS}
    assert result.new == 2
    assert result.errors == ()


def test_infojobs_is_not_asked_outside_spain(engine: Engine) -> None:
    only_infojobs = searcher(engine, adzuna=False)
    assert [s.name for s in only_infojobs.title_sources] == ["InfoJobs"]
    with pytest.raises(SearchUnavailableError, match="Netherlands"):
        only_infojobs.search(JobSearch(what="python", country="nl"))


def test_one_source_failing_keeps_the_others_results(engine: Engine) -> None:
    result = searcher(engine, adzuna=True, infojobs_status=401).search(
        JobSearch(what="python", country="es")
    )
    assert [o.source for o in result.offers] == [OfferSource.ADZUNA]
    assert result.errors == ("InfoJobs: InfoJobs refused the client id or secret",)

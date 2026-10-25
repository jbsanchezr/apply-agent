"""Company job boards, served by a fake transport in each board's real JSON shape.

The shapes were taken from the public APIs on 2026-10-24; every company,
posting and text below is invented.
"""

import json
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from apply_agent.domain.offers import OfferSource
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.search import WatchedCompany
from apply_agent.offers.boards import (
    Board,
    BoardError,
    BoardKind,
    CompanyBoards,
    board_from_url,
    slug_candidates,
)
from apply_agent.offers.search import JobSearcher, keep_offer
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository
from tests.agent.helpers import T0

GREENHOUSE = {
    "jobs": [
        {
            "id": 101,
            "title": "AI Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/arcwell/jobs/101",
            "location": {"name": "Amsterdam, Netherlands"},
            "first_published": "2026-10-20T09:00:00-04:00",
            # Greenhouse escapes the description's HTML once more.
            "content": "&lt;p&gt;You have 3+ years of experience with Python.&lt;/p&gt;",
        },
        {
            "id": 102,
            "title": "Account Executive",
            "absolute_url": "https://job-boards.greenhouse.io/arcwell/jobs/102",
            "location": {"name": "Amsterdam, Netherlands"},
            "first_published": "2026-10-21T09:00:00-04:00",
            "content": "&lt;p&gt;Sell things.&lt;/p&gt;",
        },
    ]
}
LEVER = [
    {
        "id": "a1b2c3d4-0000-4000-8000-000000000001",
        "text": "Backend Engineer",
        "hostedUrl": "https://jobs.lever.co/nubaria/a1b2c3d4-0000-4000-8000-000000000001",
        "categories": {"location": "Madrid"},
        "createdAt": 1792767640768,
        "descriptionPlain": "Build APIs in Java.",
        "lists": [{"text": "Requirements", "content": "<li>2+ years of experience</li>"}],
        "additionalPlain": "",
    }
]
ASHBY = {
    "jobs": [
        {
            "id": "f00d",
            "title": "ML Engineer",
            "location": "Remote - Europe",
            "jobUrl": "https://jobs.ashbyhq.com/brisa/f00d",
            "publishedAt": "2026-10-22T10:00:00.000+00:00",
            "isListed": True,
            "descriptionPlain": "At least four years of experience.",
        },
        {"id": "hidden", "title": "Secret Role", "isListed": False, "jobUrl": "https://x"},
    ]
}


def transport(routes: dict[str, Any], calls: list[str] | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for fragment, body in routes.items():
            if fragment in url:
                return httpx.Response(200, text=json.dumps(body))
        return httpx.Response(404, text="Not Found")

    return httpx.MockTransport(handle)


def boards(routes: dict[str, Any], calls: list[str] | None = None) -> CompanyBoards:
    return CompanyBoards(httpx.Client(transport=transport(routes, calls)), clock=lambda: T0)


@pytest.mark.parametrize(
    ("url", "board"),
    [
        ("https://job-boards.greenhouse.io/arcwell", Board(BoardKind.GREENHOUSE, "arcwell")),
        ("https://boards.greenhouse.io/arcwell/jobs/1", Board(BoardKind.GREENHOUSE, "arcwell")),
        ("https://jobs.lever.co/nubaria/abc", Board(BoardKind.LEVER, "nubaria")),
        ("https://jobs.eu.lever.co/nubaria", Board(BoardKind.LEVER_EU, "nubaria")),
        ("https://jobs.ashbyhq.com/brisa", Board(BoardKind.ASHBY, "brisa")),
        ("https://www.arcwell.example/careers", None),
    ],
)
def test_careers_urls_name_their_board(url: str, board: Board | None) -> None:
    assert board_from_url(url) == board


def test_slugs_are_tried_from_the_name() -> None:
    assert slug_candidates("Picnic Technologies B.V.") == [
        "picnictechnologies",
        "picnic-technologies",
        "picnic",
    ]
    assert slug_candidates("!!!") == []


def ashby_with(count: int) -> dict[str, Any]:
    job = ASHBY["jobs"][0]
    return {"jobs": [job | {"id": f"job-{n}", "title": f"Role {n}"} for n in range(count)]}


def test_a_company_is_found_on_the_first_board_that_surely_has_it() -> None:
    calls: list[str] = []
    found = boards({"job-board/brisa": ashby_with(5)}, calls).find("Brisa")
    assert found is not None
    assert found.board == Board(BoardKind.ASHBY, "brisa")
    assert found.confirmed
    assert [c.split("/")[2] for c in calls] == [
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.eu.lever.co",
        "api.ashbyhq.com",
    ]


def test_a_guessed_board_with_few_postings_is_only_possible() -> None:
    found = boards({"job-board/brisa": ASHBY}).find("Brisa")
    assert found is not None
    assert not found.confirmed
    assert (found.postings, found.examples) == (1, ("ML Engineer",))


def test_a_careers_url_is_always_confirmed() -> None:
    found = boards({"job-board/brisa": ASHBY}).find("https://jobs.ashbyhq.com/brisa")
    assert found is not None
    assert found.confirmed


def test_sample_postings_and_other_companies_are_not_matches() -> None:
    sample = {
        "offers": [
            {"id": 1, "title": "Senior Marketer (Sample)", "company_name": "brisa"},
        ]
    }
    assert boards({"brisa.recruitee.com": sample}).find("Brisa") is None
    other = {
        "jobs": [
            {"id": n, "title": f"Role {n}", "company_name": "Brisa Lighting Ltd"} for n in range(9)
        ]
    }
    namesake = boards({"boards/brisa": other}).find("Brisa Pay")
    # Same slug, enough postings, but the board names another company.
    assert namesake is None or not namesake.confirmed


def test_a_board_that_fails_does_not_stop_the_search() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if "greenhouse" in str(request.url):
            return httpx.Response(503)
        if "job-board/brisa" in str(request.url):
            return httpx.Response(200, text=json.dumps(ashby_with(6)))
        return httpx.Response(404)

    reader = CompanyBoards(httpx.Client(transport=httpx.MockTransport(handle)), clock=lambda: T0)
    found = reader.find("Brisa")
    assert found is not None
    assert found.board == Board(BoardKind.ASHBY, "brisa")


def test_an_empty_lever_account_is_not_a_match() -> None:
    assert boards({"postings/ghost": []}).find("Ghost") is None


def test_listings_are_read_with_descriptions_and_dates() -> None:
    reader = boards({"boards/arcwell": GREENHOUSE, "postings/nubaria": LEVER, "brisa": ASHBY})

    gh = reader.listing(Board(BoardKind.GREENHOUSE, "arcwell"), "Arcwell")
    lv = reader.listing(Board(BoardKind.LEVER, "nubaria"), "Nubaria")
    ab = reader.listing(Board(BoardKind.ASHBY, "brisa"), "Brisa")

    ai = gh.offers[0]
    assert (ai.source, ai.title, ai.company, ai.location) == (
        OfferSource.GREENHOUSE,
        "AI Engineer",
        "Arcwell",
        "Amsterdam, Netherlands",
    )
    assert ai.description == "You have 3+ years of experience with Python."
    assert ai.posted_on == date(2026, 10, 20)
    assert ai.external_id == "greenhouse/arcwell/101"
    assert lv.offers[0].description is not None
    assert "2+ years of experience" in lv.offers[0].description
    assert [o.title for o in ab.offers] == ["ML Engineer"]  # unlisted postings skipped
    assert ab.open_ids == {"ashby/brisa/f00d"}


def test_a_company_gone_from_its_board_is_an_error() -> None:
    with pytest.raises(BoardError, match="no longer on greenhouse"):
        boards({}).listing(Board(BoardKind.GREENHOUSE, "arcwell"), "Arcwell")


@pytest.mark.parametrize(
    ("title", "location", "kept"),
    [
        ("AI Engineer", "Amsterdam, Netherlands", True),
        ("AI Engineer", "Remote - Europe", True),
        ("AI Engineer", "Berlin", False),
        ("Account Executive", "Amsterdam, Netherlands", False),
    ],
)
def test_watched_postings_are_filtered_by_title_and_place(
    title: str, location: str, kept: bool
) -> None:
    offer = (
        boards({"boards/arcwell": GREENHOUSE})
        .listing(Board(BoardKind.GREENHOUSE, "arcwell"), "Arcwell")
        .offers[0]
        .model_copy(update={"title": title, "location": location})
    )
    assert keep_offer(offer, ("ai",), ("netherlands",)) is kept


def searcher(
    engine: Engine, routes: Callable[[], dict[str, Any]]
) -> tuple[JobSearcher, OfferRepository]:
    sessions = make_session_factory(engine)
    offers = OfferRepository(sessions)
    reader = CompanyBoards(
        httpx.Client(transport=httpx.MockTransport(lambda r: _route(r, routes()))),
        clock=lambda: T0,
    )
    profile = CandidateProfile(target=("ai", "engineer"))
    return JobSearcher(
        offers, SearchRepository(sessions), profile, boards=reader, clock=lambda: T0
    ), offers


def _route(request: httpx.Request, routes: dict[str, Any]) -> httpx.Response:
    for fragment, body in routes.items():
        if fragment in str(request.url):
            return httpx.Response(200, text=json.dumps(body))
    return httpx.Response(404)


def test_a_posting_missing_from_its_board_is_closed_and_reopened_if_it_returns(
    engine: Engine,
) -> None:
    listing: dict[str, Any] = {"boards/arcwell": GREENHOUSE}
    search, offers = searcher(engine, lambda: listing)
    company = WatchedCompany(id=1, name="Arcwell", board="greenhouse", slug="arcwell")

    assert search.check_company(company) == (1, 1, 0)  # the sales role is filtered out
    listing["boards/arcwell"] = {"jobs": GREENHOUSE["jobs"][1:]}
    assert search.check_company(company) == (0, 0, 1)
    [closed] = offers.list_offers()
    assert closed.closed_at == T0
    assert offers.closing_ages() == []  # board closures do not teach the fold age

    listing["boards/arcwell"] = GREENHOUSE
    search.check_company(company)
    assert offers.list_offers()[0].closed_at is None

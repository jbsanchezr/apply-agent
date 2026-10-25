"""Recruitee, Personio and SmartRecruiters, in their real shapes (checked 2026-10-24).

Every company, posting and text below is invented.
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
from apply_agent.domain.requirements import years_required
from apply_agent.domain.search import WatchedCompany
from apply_agent.offers import boards as boards_module
from apply_agent.offers.boards import Board, BoardKind, CompanyBoards, board_from_url
from apply_agent.offers.search import JobSearcher
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository
from tests.agent.helpers import T0

RECRUITEE = {
    "offers": [
        {
            "id": 2783543,
            "title": "Backend Engineer",
            "location": "Amsterdam, Noord-Holland, Netherlands",
            "city": "Amsterdam",
            "careers_url": "https://careers.nubaria.example/o/backend-engineer",
            "published_at": "2026-10-15 09:31:36 UTC",
            "description": "<p>Build our payment APIs.</p>",
            "requirements": "<ul><li>3+ years of experience with Java</li></ul>",
            "status": "published",
        },
        {"id": 1, "title": "Draft Role", "status": "draft"},
    ]
}
PERSONIO = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position>
    <id>1834171</id>
    <office>Madrid</office>
    <additionalOffices><office>Barcelona</office></additionalOffices>
    <name>Machine Learning Engineer</name>
    <jobDescriptions>
        <jobDescription>
            <name>Your mission</name>
            <value><![CDATA[<p>Ship models to production.</p>]]></value>
        </jobDescription>
    </jobDescriptions>
    <seniority>experienced</seniority>
    <yearsOfExperience>2-5</yearsOfExperience>
    <createdAt>2026-10-20T14:10:41+00:00</createdAt>
</position>
</workzag-jobs>"""


def smartrecruiters_page(total: int) -> Callable[[httpx.Request], httpx.Response]:
    """Postings 0..total-1, served page by page as SmartRecruiters does."""

    def handle(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 100))
        content = [
            {
                "id": str(9000 + n),
                "name": "AI Engineer" if n == 0 else f"Sales Role {n}",
                "company": {"name": "Brisa Group"},
                "releasedDate": "2026-10-22T01:15:03.398Z",
                "location": {"city": "Madrid", "country": "es", "fullLocation": "Madrid, Spain"},
                "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior Level"},
            }
            for n in range(offset, min(offset + limit, total))
        ]
        body = {"offset": offset, "limit": limit, "totalFound": total, "content": content}
        return httpx.Response(200, text=json.dumps(body))

    return handle


SMARTRECRUITERS_DETAIL = {
    "id": "9000",
    "postingUrl": "https://jobs.smartrecruiters.com/BrisaGroup/9000-ai-engineer",
    "jobAd": {
        "sections": {
            "companyDescription": {"text": "<p>We make scooters.</p>"},
            "jobDescription": {"text": "<p>Build agents.</p>"},
            "qualifications": {"text": "<p>At least 2 years of experience with Python.</p>"},
            "additionalInformation": {"text": ""},
        }
    },
}

Route = dict[str, Any] | list[Any] | str | Callable[[httpx.Request], httpx.Response]


def reader(routes: dict[str, Route], calls: list[str] | None = None) -> CompanyBoards:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        # Longest fragment first, so a posting's details beat its listing.
        for fragment in sorted(routes, key=len, reverse=True):
            if fragment in url:
                body = routes[fragment]
                if callable(body):
                    return body(request)
                return httpx.Response(200, text=body if isinstance(body, str) else json.dumps(body))
        return httpx.Response(404, text="Not Found")

    return CompanyBoards(httpx.Client(transport=httpx.MockTransport(handle)), clock=lambda: T0)


@pytest.mark.parametrize(
    ("url", "board"),
    [
        ("https://nubaria.recruitee.com/o/backend", Board(BoardKind.RECRUITEE, "nubaria")),
        ("https://arcwell.jobs.personio.de/job/1", Board(BoardKind.PERSONIO, "arcwell")),
        ("https://arcwell.jobs.personio.com", Board(BoardKind.PERSONIO_COM, "arcwell")),
        (
            "https://jobs.smartrecruiters.com/BrisaGroup/9000",
            Board(BoardKind.SMARTRECRUITERS, "BrisaGroup"),
        ),
        (
            "https://careers.smartrecruiters.com/BrisaGroup",
            Board(BoardKind.SMARTRECRUITERS, "BrisaGroup"),
        ),
    ],
)
def test_careers_urls_of_the_new_boards(url: str, board: Board) -> None:
    assert board_from_url(url) == board


def test_recruitee_postings_are_read_with_their_requirements() -> None:
    listing = reader({"nubaria.recruitee.com": RECRUITEE}).listing(
        Board(BoardKind.RECRUITEE, "nubaria"), "Nubaria"
    )
    [offer] = listing.offers  # the draft is skipped
    assert offer.source is OfferSource.RECRUITEE
    assert offer.posted_on == date(2026, 10, 15)
    assert offer.url == "https://careers.nubaria.example/o/backend-engineer"
    assert years_required(offer.description) == 3
    assert listing.complete


def test_personio_states_its_years_as_a_code() -> None:
    board = Board(BoardKind.PERSONIO, "arcwell")
    [offer] = reader({"arcwell.jobs.personio.de/xml": PERSONIO}).listing(board, "Arcwell").offers
    assert (offer.source, offer.title, offer.location) == (
        OfferSource.PERSONIO,
        "Machine Learning Engineer",
        "Madrid, Barcelona",
    )
    assert offer.url == "https://arcwell.jobs.personio.de/job/1834171"
    assert offer.description is not None
    assert "Ship models to production." in offer.description
    assert years_required(offer.description) == 2


def test_a_personio_page_that_is_not_its_feed_is_not_a_board() -> None:
    page = reader({"arcwell.jobs.personio.de/xml": "<html><body>Not here</body></html>"})
    assert page.find("https://arcwell.jobs.personio.de") is None


def test_smartrecruiters_is_found_from_its_first_page_only() -> None:
    calls: list[str] = []
    found = reader({"companies/brisagroup/postings": smartrecruiters_page(250)}, calls).find(
        "BrisaGroup"
    )
    assert found is not None
    assert found.board == Board(BoardKind.SMARTRECRUITERS, "brisagroup")
    assert found.confirmed  # "BrisaGroup" is the "Brisa Group" the postings name
    assert sum("smartrecruiters" in c for c in calls) == 1


def test_an_empty_smartrecruiters_listing_means_the_company_is_not_there() -> None:
    assert reader({"companies/ghost/postings": smartrecruiters_page(0)}).find("Ghost") is None


def test_a_capped_smartrecruiters_listing_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boards_module, "SMARTRECRUITERS_MAX", 200)
    board = Board(BoardKind.SMARTRECRUITERS, "BrisaGroup")
    full = reader({"BrisaGroup/postings": smartrecruiters_page(150)}).listing(board, "Brisa")
    capped = reader({"BrisaGroup/postings": smartrecruiters_page(500)}).listing(board, "Brisa")
    assert (len(full.offers), full.complete) == (150, True)
    assert (len(capped.offers), capped.complete) == (200, False)


def test_smartrecruiters_details_fill_the_description() -> None:
    routes: dict[str, Route] = {
        "BrisaGroup/postings": smartrecruiters_page(3),
        "BrisaGroup/postings/9000": SMARTRECRUITERS_DETAIL,
    }
    board = Board(BoardKind.SMARTRECRUITERS, "BrisaGroup")
    boards = reader(routes)
    listing = boards.listing(board, "Brisa")
    assert listing.offers[0].description is None
    [described] = boards.describe(board, listing.offers[:1])
    assert described.description is not None
    assert "Build agents." in described.description
    assert years_required(described.description) == 2
    assert described.url == "https://jobs.smartrecruiters.com/BrisaGroup/9000-ai-engineer"


def test_details_are_fetched_once_and_a_partial_listing_closes_nothing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(boards_module, "SMARTRECRUITERS_MAX", 100)
    calls: list[str] = []
    total = {"n": 50}
    routes: dict[str, Route] = {
        "BrisaGroup/postings": lambda r: smartrecruiters_page(total["n"])(r),
        "BrisaGroup/postings/9000": SMARTRECRUITERS_DETAIL,
    }
    sessions = make_session_factory(engine)
    offers = OfferRepository(sessions)
    search = JobSearcher(
        offers,
        SearchRepository(sessions),
        CandidateProfile(target=("ai",)),
        boards=reader(routes, calls),
        clock=lambda: T0,
    )
    company = WatchedCompany(id=1, name="Brisa", board="smartrecruiters", slug="BrisaGroup")

    assert search.check_company(company) == (1, 1, 0)
    assert search.check_company(company) == (1, 0, 0)
    assert sum(c.endswith("/postings/9000") for c in calls) == 1  # details only when new
    [stored] = offers.list_offers()
    assert years_required(stored.offer.description) == 2

    # The AI posting falls past the cap: the listing is partial, so it is not closed.
    total["n"] = 5000
    routes["BrisaGroup/postings"] = lambda r: _without_first(smartrecruiters_page(5000)(r))
    search.check_company(company)
    assert offers.list_offers()[0].closed_at is None


def _without_first(response: httpx.Response) -> httpx.Response:
    body = response.json()
    body["content"] = [job for job in body["content"] if job["id"] != "9000"]
    return httpx.Response(200, text=json.dumps(body))

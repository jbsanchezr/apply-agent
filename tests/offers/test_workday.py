"""Workday, in its real shape (checked 2026-10-26). Every company, posting and text is invented."""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest
from sqlalchemy import Engine

from apply_agent.domain.offers import OfferSource
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.requirements import years_required
from apply_agent.domain.search import WatchedCompany
from apply_agent.offers import boards as boards_module
from apply_agent.offers.boards import Board, BoardKind, board_from_url
from apply_agent.offers.search import JobSearcher
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository
from tests.agent.helpers import T0
from tests.offers.test_more_boards import Route, reader

BOARD = Board(BoardKind.WORKDAY, "brisa.wd3.BrisaCareers")
LISTING = "brisa.wd3.myworkdayjobs.com/wday/cxs/brisa/BrisaCareers/jobs"
DETAIL = "wday/cxs/brisa/BrisaCareers/job/Amsterdam/AI-Engineer_R-100"
POSTED = ["Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"]


def workday_pages(total: int) -> Callable[[httpx.Request], httpx.Response]:
    """Postings 0..total-1, 20 a page; as Workday does, only the first page says the total."""

    def handle(request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)
        offset, limit = asked["offset"], asked["limit"]
        postings = [
            {
                "title": "AI Engineer" if n == 0 else f"Sales Role {n}",
                "externalPath": "/job/Amsterdam/AI-Engineer_R-100" if n == 0 else f"/job/X/S_{n}",
                "locationsText": "2 Locations" if n == 0 else "Madrid",
                "postedOn": POSTED[n % len(POSTED)],
                "bulletFields": [f"R-{100 + n}"],
            }
            for n in range(offset, min(offset + limit, total))
        ]
        body = {"total": total if offset == 0 else 0, "jobPostings": postings}
        return httpx.Response(200, text=json.dumps(body))

    return handle


WORKDAY_DETAIL = {
    "jobPostingInfo": {
        "title": "AI Engineer",
        "jobDescription": "<p>Build agents.</p><p>2+ years of experience with Python</p>",
        "location": "Amsterdam",
        "additionalLocations": ["Utrecht"],
        "country": {"descriptor": "Netherlands"},
        "startDate": "2026-10-20",
        "externalUrl": "https://brisa.wd3.myworkdayjobs.com/BrisaCareers/job/Amsterdam/AI-Engineer_R-100",
    }
}


@pytest.mark.parametrize(
    "url",
    [
        "https://brisa.wd3.myworkdayjobs.com/BrisaCareers",
        "https://brisa.wd3.myworkdayjobs.com/en-US/BrisaCareers/job/Amsterdam/AI-Engineer_R-100",
        "brisa.wd3.myworkdayjobs.com/BrisaCareers?q=python",
    ],
)
def test_a_workday_careers_link_names_its_board(url: str) -> None:
    assert board_from_url(url) == BOARD
    assert BOARD.careers_url == "https://brisa.wd3.myworkdayjobs.com/BrisaCareers"
    assert BOARD.company_name == "brisa"


def test_a_workday_slug_needs_its_server_and_site() -> None:
    with pytest.raises(ValueError, match="Workday"):
        Board(BoardKind.WORKDAY, "brisa")


def test_workday_is_found_by_its_link_never_by_name() -> None:
    calls: list[str] = []
    boards = reader({LISTING: workday_pages(3)}, calls)
    match = boards.find("https://brisa.wd3.myworkdayjobs.com/BrisaCareers")
    assert match is not None
    assert match.confirmed
    assert match.board == BOARD
    calls.clear()
    boards.find("Brisa")
    assert not any("myworkdayjobs" in c for c in calls)


def test_a_wrong_workday_site_is_not_a_board() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="{}")

    assert reader({LISTING: refuse}).find(BOARD.careers_url) is None


def test_workday_listings_are_paged_and_dated() -> None:
    listing = reader({LISTING: workday_pages(45)}).listing(BOARD, "Brisa")
    assert len(listing.offers) == 45
    assert listing.complete
    first, second, third, fourth = listing.offers[:4]
    assert first.source is OfferSource.WORKDAY
    assert first.external_id == "workday/brisa.wd3.BrisaCareers/R-100"
    assert first.location is None  # "2 Locations": the details say which
    assert (
        first.url
        == "https://brisa.wd3.myworkdayjobs.com/BrisaCareers/job/Amsterdam/AI-Engineer_R-100"
    )
    assert [o.posted_on for o in (first, second, third, fourth)] == [
        T0.date(),
        date.fromordinal(T0.date().toordinal() - 1),
        date.fromordinal(T0.date().toordinal() - 3),
        None,  # "30+ Days Ago" is no date
    ]


def test_workday_details_fill_the_description_and_places() -> None:
    boards = reader({LISTING: workday_pages(3), DETAIL: WORKDAY_DETAIL})
    listing = boards.listing(BOARD, "Brisa")
    [described] = boards.describe(BOARD, listing.offers[:1])
    assert described.description is not None
    assert years_required(described.description) == 2
    assert described.location == "Amsterdam; Utrecht, Netherlands"
    assert described.posted_on == date(2026, 10, 20)


def test_a_capped_workday_listing_keeps_details_and_closes_nothing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(boards_module, "WORKDAY_MAX", 40)
    calls: list[str] = []
    routes: dict[str, Route] = {LISTING: workday_pages(30), DETAIL: WORKDAY_DETAIL}
    sessions = make_session_factory(engine)
    offers = OfferRepository(sessions)
    search = JobSearcher(
        offers,
        SearchRepository(sessions),
        CandidateProfile(target=("ai",)),
        boards=reader(routes, calls),
        clock=lambda: T0,
    )
    company = WatchedCompany(id=1, name="Brisa", board="workday", slug=BOARD.slug)

    assert search.check_company(company) == (1, 1, 0)
    assert search.check_company(company) == (1, 0, 0)
    assert sum(DETAIL in c for c in calls) == 1  # details only when new
    [stored] = offers.list_offers()
    # Read again from the listing, it keeps what its own page said.
    assert stored.offer.location == "Amsterdam; Utrecht, Netherlands"
    assert stored.offer.posted_on == date(2026, 10, 20)

    # Thousands listed and the AI posting not among those read: the listing is
    # partial, so its absence closes nothing.
    pages = workday_pages(2000)

    def without_ai(request: httpx.Request) -> httpx.Response:
        body = pages(request).json()
        body["jobPostings"] = [p for p in body["jobPostings"] if p["title"] != "AI Engineer"]
        return httpx.Response(200, text=json.dumps(body))

    routes[LISTING] = without_ai
    search.check_company(company)
    assert offers.list_offers()[0].closed_at is None

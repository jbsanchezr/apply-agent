"""The user's list of companies: parsing a pasted list and finding each one's board."""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.search import BoardStatus
from apply_agent.offers.boards import CompanyBoards
from apply_agent.offers.search import JobSearcher
from apply_agent.offers.targets import check_targets
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository
from apply_agent.storage.targets import TargetRepository, parse_company_list
from tests.agent.helpers import T0


def test_a_pasted_list_keeps_its_groups_and_reads_and() -> None:
    text = (
        "Empresas objetivo: Arcwell, Brisa Pay, Nubaria y Corvina Retail.\n"
        "London: Pendulum Health; Arcwell Inc\n"
        "Delfos and Tessellate"
    )
    assert parse_company_list(text) == [
        ("Empresas objetivo", "Arcwell"),
        ("Empresas objetivo", "Brisa Pay"),
        ("Empresas objetivo", "Nubaria"),
        ("Empresas objetivo", "Corvina Retail"),
        ("London", "Pendulum Health"),  # "Arcwell Inc" is Arcwell again
        (None, "Delfos"),
        (None, "Tessellate"),
    ]


def test_adding_twice_keeps_one_of_each(engine: Engine) -> None:
    targets = TargetRepository(make_session_factory(engine))
    assert targets.add([("NL", "Arcwell"), ("NL", "Brisa Pay")]) == 2
    assert targets.add([(None, "ARCWELL B.V."), (None, "Nubaria")]) == 1
    assert [t.name for t in targets.list()] == ["Arcwell", "Brisa Pay", "Nubaria"]
    assert all(t.status is BoardStatus.PENDING for t in targets.list())


def _jobs(count: int, company: str) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": n,
                "title": f"AI Engineer {n}",
                "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{n}",
                "location": {"name": "Amsterdam"},
                "company_name": company,
                "content": "",
            }
            for n in range(count)
        ]
    }


@pytest.fixture
def setup(engine: Engine) -> tuple[TargetRepository, JobSearcher, SearchRepository]:
    routes = {
        "boards/arcwell/": _jobs(6, "Arcwell"),  # sure
        "boards/brisa/": _jobs(2, "Brisa"),  # few postings: possible
        "boards/nubaria/": None,  # the board fails
    }

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for fragment, body in routes.items():
            if fragment in url:
                if body is None:
                    return httpx.Response(500)
                return httpx.Response(200, text=json.dumps(body))
        return httpx.Response(404)

    sessions = make_session_factory(engine)
    searches = SearchRepository(sessions)
    searcher = JobSearcher(
        OfferRepository(sessions),
        searches,
        CandidateProfile(target=("ai",)),
        boards=CompanyBoards(httpx.Client(transport=httpx.MockTransport(handle)), clock=lambda: T0),
        clock=lambda: T0,
    )
    targets = TargetRepository(sessions)
    targets.add([(None, "Arcwell"), (None, "Brisa"), (None, "Nubaria"), (None, "Delfos")])
    return targets, searcher, searches


def test_each_company_gets_a_board_status_and_sure_ones_are_watched(
    setup: tuple[TargetRepository, JobSearcher, SearchRepository],
) -> None:
    targets, searcher, searches = setup

    report = check_targets(targets, searcher, lambda: T0)

    status = {t.name: (t.status, t.board, t.slug) for t in targets.list()}
    assert status == {
        "Arcwell": (BoardStatus.FOUND, "greenhouse", "arcwell"),
        "Brisa": (BoardStatus.POSSIBLE, "greenhouse", "brisa"),
        "Nubaria": (BoardStatus.NONE, None, None),  # its board failed, the others had nothing
        "Delfos": (BoardStatus.NONE, None, None),
    }
    brisa = next(t for t in targets.list() if t.name == "Brisa")
    assert brisa.note == "2 posting(s), e.g. AI Engineer 0; AI Engineer 1"
    assert [c.name for c in searches.list_companies()] == ["Arcwell"]
    assert (report.checked, report.found, report.possible, report.none) == (4, 1, 1, 2)

    again = check_targets(targets, searcher, lambda: T0)
    assert again.checked == 0  # only pending companies are looked for

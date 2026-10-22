from datetime import timedelta

import httpx
import pytest
from sqlalchemy import Engine

from apply_agent.config import LinkedInSearch
from apply_agent.domain.offers import JobOffer, OfferSource, OfferState
from apply_agent.offers.linkedin_search import SearchRefusedError
from apply_agent.offers.scanner import OfferScanner
from apply_agent.providers import FakeEmailProvider
from apply_agent.storage import make_session_factory
from apply_agent.storage.offers import OfferNotFoundError, OfferRepository
from tests.agent.helpers import T0, ListProvider
from tests.conftest import FIXTURES_DIR
from tests.offers.test_alerts import ENGLISH_ALERT, REAL_ALERT, alert

LOOKBACK = timedelta(days=3650)


@pytest.fixture
def offers(engine: Engine) -> OfferRepository:
    return OfferRepository(make_session_factory(engine))


class FakeSearch:
    def __init__(self, results: dict[str, list[JobOffer] | Exception]) -> None:
        self.results = results
        self.asked: list[str] = []

    def search(self, query: LinkedInSearch) -> list[JobOffer]:
        self.asked.append(query.keywords)
        result = self.results[query.keywords]
        if isinstance(result, Exception):
            raise result
        return result


def found(title: str, linkedin_id: str) -> JobOffer:
    return JobOffer(
        source=OfferSource.LINKEDIN_SEARCH,
        linkedin_id=linkedin_id,
        title=title,
        company="Arcwell & Co",
        seen_at=T0 + timedelta(days=1),
    )


def test_reads_alerts_from_the_inbox_once(offers: OfferRepository) -> None:
    provider = FakeEmailProvider(FIXTURES_DIR / "emails")
    scanner = OfferScanner(provider, offers, initial_lookback=LOOKBACK)

    first = scanner.scan()
    second = scanner.scan()

    assert (first.alert_emails, first.offers_found, first.new_offers) == (1, 3, 3)
    assert (second.alert_emails, second.offers_found) == (0, 0)
    assert {o.offer.company for o in offers.list_offers()} == {
        "Brisa Mobility",
        "Corvina Retail",
        "Pendulum Health",
    }


def test_asks_the_provider_only_for_linkedin_mail(offers: OfferRepository) -> None:
    other = alert(ENGLISH_ALERT).model_copy(update={"id": "m2", "sender": "a@b.example"})
    provider = ListProvider([alert(ENGLISH_ALERT), other])
    report = OfferScanner(provider, offers, initial_lookback=LOOKBACK).scan()
    assert report.new_offers == 2


def test_a_search_result_seen_again_keeps_the_users_decision(offers: OfferRepository) -> None:
    provider = ListProvider([alert(ENGLISH_ALERT)])
    OfferScanner(provider, offers, initial_lookback=LOOKBACK).scan()
    [first, *_] = offers.list_offers()
    offers.set_state(first.id, OfferState.DISMISSED)

    search = FakeSearch({"data engineer": [found("Data Engineer", "4100000001")]})
    report = OfferScanner(
        ListProvider([]),
        offers,
        initial_lookback=LOOKBACK,
        searches=(LinkedInSearch(keywords="data engineer"),),
        search=search,  # type: ignore[arg-type]
    ).scan()

    assert (report.searches_run, report.offers_found, report.new_offers) == (1, 1, 0)
    again = next(o for o in offers.list_offers() if o.offer.linkedin_id == "4100000001")
    assert again.state is OfferState.DISMISSED
    assert again.last_seen_at == T0 + timedelta(days=1)


def test_a_refusal_skips_the_remaining_searches(offers: OfferRepository) -> None:
    search = FakeSearch(
        {
            "a": httpx.ConnectError("down"),
            "b": SearchRefusedError("LinkedIn refused the search (HTTP 429)"),
            "c": [found("C", "4100000009")],
        }
    )
    report = OfferScanner(
        ListProvider([]),
        offers,
        initial_lookback=LOOKBACK,
        searches=tuple(LinkedInSearch(keywords=k) for k in "abc"),
        search=search,  # type: ignore[arg-type]
    ).scan()

    assert search.asked == ["a", "b"]
    assert report.searches_run == 0
    assert report.search_errors[0] == "a @ anywhere: ConnectError"
    assert "429" in report.search_errors[1]


def test_a_rescan_replaces_misread_offers_and_keeps_decisions(offers: OfferRepository) -> None:
    misread = JobOffer(
        source=OfferSource.LINKEDIN_ALERT,
        linkedin_id="4100000012",
        title="This company is actively hiring",
        company="Apply with resume & profile",
        seen_at=T0,
    )
    offers.save([misread])
    provider = ListProvider([alert(REAL_ALERT)])
    scanner = OfferScanner(provider, offers, initial_lookback=LOOKBACK)
    scanner.scan()
    kept = next(o for o in offers.list_offers() if o.offer.linkedin_id == "4100000011")
    offers.set_state(kept.id, OfferState.INTERESTED)

    removed, report = scanner.rescan()

    assert removed == 1
    assert report.alert_emails == 1
    by_id = {o.offer.linkedin_id: o for o in offers.list_offers()}
    assert by_id["4100000012"].offer.title.startswith("Desarrollador/a Python")
    assert by_id["4100000011"].state is OfferState.INTERESTED
    assert len(by_id) == 3


def test_deciding_about_an_unknown_offer_fails(offers: OfferRepository) -> None:
    with pytest.raises(OfferNotFoundError):
        offers.set_state(404, OfferState.INTERESTED)


def test_a_closed_offer_remembers_its_age_and_can_be_reopened(offers: OfferRepository) -> None:
    offers.save([found("Data Engineer", "4100000021")])
    [stored] = offers.list_offers()

    closed = offers.mark_closed(stored.id, stored.last_seen_at + timedelta(days=12, hours=5))

    assert closed.closed_at == stored.last_seen_at + timedelta(days=12, hours=5)
    assert offers.closing_ages() == [12]
    assert offers.list_offers()[0].closed_at is not None
    assert offers.reopen(stored.id).closed_at is None
    assert offers.closing_ages() == []


def test_deleting_an_offer_deletes_its_closure(offers: OfferRepository) -> None:
    offers.save([found("Data Engineer", "4100000022")])
    [stored] = offers.list_offers()
    offers.mark_closed(stored.id, stored.last_seen_at)
    assert offers.delete_offers([stored.id]) == 1
    assert offers.closing_ages() == []
    with pytest.raises(OfferNotFoundError):
        offers.mark_closed(stored.id, T0)

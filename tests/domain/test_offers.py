from datetime import timedelta

import pytest
from pydantic import ValidationError

from apply_agent.domain import Application, ApplicationStatus
from apply_agent.domain.offers import (
    JobOffer,
    OfferMatch,
    OfferSource,
    match_offer,
    typical_closing_age,
)
from tests.agent.helpers import T0


def offer(title: str = "Data Engineer", company: str = "Arcwell", **extra: object) -> JobOffer:
    return JobOffer.model_validate(
        {"source": OfferSource.LINKEDIN_ALERT, "title": title, "company": company, "seen_at": T0}
        | extra
    )


def application(
    company: str,
    role: str | None,
    status: ApplicationStatus = ApplicationStatus.APPLIED,
    *,
    id: int = 1,
    days: int = 0,
) -> Application:
    return Application(
        id=id,
        company=company,
        role=role,
        status=status,
        first_seen_at=T0,
        last_activity_at=T0 + timedelta(days=days),
        thread_ids=frozenset({f"t{id}"}),
    )


def test_the_linkedin_id_identifies_a_posting() -> None:
    assert offer(linkedin_id="4100000001").key == "linkedin:4100000001"


def test_without_an_id_cosmetic_differences_do_not_make_a_new_posting() -> None:
    a = offer("Data Engineer", "Arcwell, Inc.", location="Madrid")
    b = offer("data  engineer", "ARCWELL", location="madrid")
    c = offer("Data Engineer", "Arcwell", location="Barcelona")
    assert a.key == b.key != c.key


@pytest.mark.parametrize("url", ["http://www.linkedin.com/jobs/view/1/", "javascript:alert(1)"])
def test_only_https_links_are_kept(url: str) -> None:
    with pytest.raises(ValidationError):
        offer(url=url)


def test_same_company_and_role_is_an_application() -> None:
    apps = [application("Arcwell Inc", "Data Engineer", ApplicationStatus.INTERVIEWING, id=7)]
    match = match_offer(offer(), apps)
    assert match.kind is OfferMatch.APPLIED
    assert match.application_id == 7
    assert match.application_status is ApplicationStatus.INTERVIEWING


def test_a_title_with_a_longer_tail_is_the_same_role() -> None:
    apps = [application("Corvina Retail", "Senior ML Engineer")]
    match = match_offer(offer("Senior ML Engineer, Recommendations", "Corvina Retail"), apps)
    assert match.kind is OfferMatch.APPLIED


def test_another_role_at_the_company_is_only_a_hint() -> None:
    apps = [
        application("Arcwell", "Product Designer", id=1, days=1),
        application("Arcwell Labs", None, ApplicationStatus.REJECTED, id=2, days=5),
    ]
    match = match_offer(offer(), apps)
    assert match.kind is OfferMatch.SAME_COMPANY
    assert match.application_id == 2  # the most recent news


def test_an_unknown_company_is_no_match() -> None:
    assert match_offer(offer(), [application("Brisa", "Data Engineer")]).kind is OfferMatch.NONE


def test_the_closing_age_is_learned_only_from_enough_closures() -> None:
    assert typical_closing_age([]) is None
    assert typical_closing_age([9, 9, 9, 9]) is None
    assert typical_closing_age([4, 9, 10, 12, 30]) == 10


def test_a_learned_age_is_never_shorter_than_the_minimum() -> None:
    assert typical_closing_age([0, 0, 1, 1, 2]) == 3

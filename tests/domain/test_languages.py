"""Language requirements in job postings. The sentences mirror real postings."""

import pytest

from apply_agent.domain.fit import FitLevel, assess_fit
from apply_agent.domain.languages import (
    language_named,
    language_needs,
    title_language,
    unmet,
    written_in,
)
from apply_agent.domain.offers import JobOffer, OfferSource
from apply_agent.domain.profile import CandidateProfile
from tests.agent.helpers import T0

SPEAKS = ("spanish", "english")


@pytest.mark.parametrize(
    ("text", "lacking"),
    [
        ("Excellent communication skills and fluency in English and Dutch", ["dutch"]),
        ("Fluent in English and German (any additional language is a plus).", ["german"]),
        ("Verhandlungssichere Deutsch- und Englischkenntnisse", ["german"]),
        ("Sehr gute Deutschkenntnisse in Wort und Schrift", ["german"]),
        ("Je spreekt vloeiend Nederlands en Engels.", ["dutch"]),
        ("Backend Developer berichtenplatform - IT Operatie (Dutch only)", ["dutch"]),
        ("Senior Engineer (German speaking)", ["german"]),
        (
            "Fluency in English and at least one Nordic language (Finnish, Danish, Swedish)",
            ["danish", "finnish", "swedish"],
        ),
        # Alternatives: speaking one of them is enough.
        ("Fluency in French and/or Spanish", []),
        ("Spanish or Portuguese is required", []),
        # A plus is not a requirement, and a mere mention is nothing.
        ("Dutch is a plus.", []),
        ("Knowledge of German is an advantage", []),
        ("Our docs are translated into German.", []),
        ("You work with colleagues in Germany and English is our language.", []),
        ("Fluent English is required.", []),
    ],
)
def test_required_languages_are_found(text: str, lacking: list[str]) -> None:
    assert sorted(unmet(language_needs(text), SPEAKS)) == lacking


def test_a_plus_is_kept_apart_from_a_requirement() -> None:
    [need] = language_needs("Other (European) languages, such as German, are a strong plus")
    assert (need.language, need.required) == ("german", False)


def test_languages_are_named_in_several_languages() -> None:
    assert language_named("Nederlands") == "dutch"
    assert language_named("alemán") == "german"
    assert language_named("Klingon") is None


def test_the_language_a_posting_is_written_in() -> None:
    dutch = (
        "Wij zoeken een ervaren ontwikkelaar voor ons team in Utrecht. Je werkt met onze "
        "klanten aan het platform en je bent verantwoordelijk voor de backend. " * 4
    )
    english = "We are looking for an engineer to join our team and build the platform. " * 6
    assert written_in(dutch) == "dutch"
    assert written_in(english) == "english"
    assert written_in("Kort.") is None
    assert title_language("Python-ontwikkelaar") == "dutch"
    assert title_language("AI/ML-utvecklare") == "swedish"
    assert title_language("Machine Learning Engineer (m/f/d)") is None  # not a language


PROFILE = CandidateProfile(target=("backend",), languages=SPEAKS)


def offer(title: str, description: str | None = None) -> JobOffer:
    return JobOffer(
        source=OfferSource.GREENHOUSE,
        title=title,
        company="Arcwell",
        description=description,
        seen_at=T0,
    )


def test_a_language_the_candidate_lacks_sinks_the_offer() -> None:
    fit = assess_fit(offer("Junior Backend Engineer", "Fluent in English and German."), PROFILE)
    assert fit.level is FitLevel.UNLIKELY
    assert fit.score == 15
    assert "The posting asks for German; you speak Spanish, English" in fit.reasons


def test_a_posting_in_another_language_sinks_too() -> None:
    fit = assess_fit(offer("Junior Backend ontwikkelaar"), PROFILE)
    assert "The posting is written in Dutch; you speak Spanish, English" in fit.reasons
    assert fit.level is FitLevel.UNLIKELY


def test_a_plus_is_only_noted() -> None:
    plain = assess_fit(offer("Junior Backend Engineer"), PROFILE)
    plus = assess_fit(offer("Junior Backend Engineer", "Dutch is a plus."), PROFILE)
    assert plus.score == plain.score
    assert "Dutch is a plus" in plus.reasons


def test_what_the_company_asked_before_lowers_but_does_not_sink() -> None:
    fit = assess_fit(offer("Junior Backend Engineer"), PROFILE, asked_before=("dutch",))
    assert fit.score == 35
    assert "This company asked for Dutch before" in fit.reasons
    spoken = assess_fit(offer("Junior Backend Engineer"), PROFILE, asked_before=("english",))
    assert spoken.score > 35


def test_without_languages_in_the_profile_nothing_is_judged() -> None:
    no_languages = PROFILE.model_copy(update={"languages": ()})
    fit = assess_fit(offer("Junior Backend Engineer", "Fluent in German."), no_languages)
    assert fit.level is FitLevel.GOOD

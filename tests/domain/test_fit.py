import pytest

from apply_agent.domain.fit import FitLevel, Seniority, assess_fit, seniority
from apply_agent.domain.offers import JobOffer, OfferSource
from apply_agent.domain.profile import Area, CandidateProfile
from tests.agent.helpers import T0

PROFILE = CandidateProfile(
    years_experience=3,
    max_years_asked=5,
    target=("ai engineer", "llm", "backend", "python"),
    avoid=("devops", "phd", "freelance", "front end"),
    preferred_places=("amsterdam", "madrid"),
    bonus=("fashion", "second hand"),
)


def offer(title: str, company: str = "Arcwell", location: str | None = None) -> JobOffer:
    return JobOffer(
        source=OfferSource.LINKEDIN_ALERT,
        title=title,
        company=company,
        location=location,
        seen_at=T0,
    )


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Junior Backend Developer - Madrid", Seniority.ENTRY),
        ("Graduate Software Engineer", Seniority.ENTRY),
        ("Becario/a de Desarrollo IA", Seniority.ENTRY),
        ("Ingeniero en prácticas", Seniority.ENTRY),
        ("Mid Forward Deployed Engineer", Seniority.MID),
        ("Software Engineer II", Seniority.MID),
        ("Senior ML Engineer", Seniority.SENIOR),
        ("Sr. Data Engineer", Seniority.SENIOR),
        ("Ingeniero Sénior de IA", Seniority.SENIOR),
        ("Expert AI Agentic Developer", Seniority.SENIOR),
        ("Lead AI Engineer", Seniority.LEAD),
        ("AI Solutions Architect | Remote EU", Seniority.LEAD),
        ("Senior Staff Engineer", Seniority.LEAD),
        ("Member of Technical Staff - ML Data", Seniority.UNSTATED),
        ("AI Engineer", Seniority.UNSTATED),
        ("Backend Engineer (Python)", Seniority.UNSTATED),
    ],
)
def test_seniority_is_read_from_the_title(title: str, level: Seniority) -> None:
    assert seniority(title) is level


def test_years_asked_are_compared_with_the_ceiling() -> None:
    graduate = CandidateProfile()
    assert assess_fit(offer("Senior Engineer"), graduate).level is FitLevel.UNLIKELY
    assert assess_fit(offer("Junior Engineer"), graduate).level is FitLevel.GOOD
    within_reach = assess_fit(offer("Senior Engineer"), PROFILE)
    assert within_reach.level is FitLevel.POSSIBLE
    assert within_reach.reasons[0] == "A senior title, usually 5+ years; you have 3 in total"
    assert assess_fit(offer("Medior Engineer"), PROFILE).level is FitLevel.GOOD
    lead = assess_fit(
        offer("Lead Engineer"), CandidateProfile(years_experience=3, max_years_asked=6)
    )
    assert lead.level is FitLevel.UNLIKELY
    assert lead.reasons[0].endswith("beyond your limit of 6")
    assert assess_fit(offer("Lead Engineer"), PROFILE).level is FitLevel.UNLIKELY


def test_without_a_seniority_word_wanted_words_decide() -> None:
    plain = assess_fit(offer("Data Analyst"), PROFILE)
    wanted = assess_fit(offer("AI Engineer (LLM)"), PROFILE)

    assert plain.level is FitLevel.POSSIBLE
    assert wanted.level is FitLevel.GOOD
    assert wanted.score == plain.score + 15
    assert wanted.reasons == (
        "No seniority in the title, assumed ~2 years; you have 3 in total",
        "Wanted: ai engineer, llm",
    )


def test_words_match_whole_words_only() -> None:
    assert assess_fit(offer("Email Engineer"), CandidateProfile(target=("ai",))).score == 40


def test_preferred_place_and_bonus_add_points() -> None:
    base = assess_fit(offer("Data Analyst"), PROFILE).score
    placed = assess_fit(offer("Data Analyst", location="Amsterdam, North Holland"), PROFILE)
    bonus = assess_fit(offer("Data Analyst", company="Second-Hand Fashion Co"), PROFILE)

    assert placed.score == base + 10
    assert "Preferred place: amsterdam" in placed.reasons
    assert bonus.score == base + 10
    assert "Bonus: fashion, second hand" in bonus.reasons


def test_an_avoided_word_sinks_the_offer_whatever_else_matches() -> None:
    fit = assess_fit(offer("Python Backend Engineer (Freelance)", location="Madrid"), PROFILE)
    assert fit.level is FitLevel.UNLIKELY
    assert fit.score == 15
    assert "Avoided: freelance" in fit.reasons
    assert assess_fit(offer("Front-End Developer"), PROFILE).level is FitLevel.UNLIKELY


def test_the_ceiling_cannot_be_below_the_experience() -> None:
    with pytest.raises(ValueError, match="max_years_asked"):
        CandidateProfile(years_experience=4, max_years_asked=2)


AREAS = CandidateProfile(
    years_experience=3,
    max_years_asked=5,
    areas={
        "backend": Area(years=3, words=("backend", "java")),
        "ai": Area(years=0, words=("ai", "ml", "llm")),
    },
)


@pytest.mark.parametrize(
    ("title", "level", "reason"),
    [
        (
            "Backend Engineer",
            FitLevel.POSSIBLE,
            "No seniority in the title, assumed ~2 years; you have 3 in backend",
        ),
        (
            "AI Engineer",
            FitLevel.POSSIBLE,
            "No seniority in the title, assumed ~2 years; you have 0 in ai",
        ),
        ("Junior AI Engineer", FitLevel.GOOD, "An entry-level title; you have 0 in ai"),
        (
            "Senior AI Engineer",
            FitLevel.UNLIKELY,
            "A senior title, usually 5+ years; you have only 0 in ai",
        ),
        # A title in two areas asks for both, so the weaker one counts.
        (
            "AI Backend Engineer",
            FitLevel.POSSIBLE,
            "No seniority in the title, assumed ~2 years; you have 0 in ai",
        ),
        (
            "Data Engineer",
            FitLevel.POSSIBLE,
            "No seniority in the title, assumed ~2 years; you have 3 in total",
        ),
    ],
)
def test_experience_is_counted_in_the_titles_area(title: str, level: FitLevel, reason: str) -> None:
    fit = assess_fit(offer(title), AREAS)
    assert fit.level is level
    assert fit.reasons[0] == reason


def test_ai_titles_without_seniority_rank_below_backend_ones() -> None:
    backend = assess_fit(offer("Backend Engineer"), AREAS)
    ai = assess_fit(offer("AI Engineer"), AREAS)
    assert backend.score == 65
    assert ai.score == 40

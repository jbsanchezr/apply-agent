import pytest

from apply_agent.domain.fit import FitLevel, Seniority, assess_fit, seniority
from apply_agent.domain.offers import JobOffer, OfferSource
from apply_agent.domain.profile import Area, CandidateProfile
from apply_agent.domain.requirements import years_required
from tests.agent.helpers import T0

PROFILE = CandidateProfile(
    years_experience=3,
    max_years_asked=5,
    target=("ai engineer", "llm", "backend", "python"),
    avoid=("devops", "phd", "freelance", "front end"),
    preferred_places=("amsterdam", "madrid"),
    bonus=("fashion", "second hand"),
)


def offer(
    title: str,
    company: str = "Arcwell",
    location: str | None = None,
    description: str | None = None,
) -> JobOffer:
    return JobOffer(
        source=OfferSource.LINKEDIN_ALERT,
        title=title,
        company=company,
        location=location,
        description=description,
        seen_at=T0,
    )


# A posting whose description names no years: the title's guess stands.
SILENT = "You will build payment tools with a small team."


def described(title: str, **kwargs: str) -> JobOffer:
    return offer(title, description=SILENT, **kwargs)


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
    plain = assess_fit(described("Data Analyst"), PROFILE)
    wanted = assess_fit(described("AI Engineer (LLM)"), PROFILE)

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
    base = assess_fit(described("Data Analyst"), PROFILE).score
    placed = assess_fit(described("Data Analyst", location="Amsterdam, North Holland"), PROFILE)
    bonus = assess_fit(described("Data Analyst", company="Second-Hand Fashion Co"), PROFILE)

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
    backend = assess_fit(described("Backend Engineer"), AREAS)
    ai = assess_fit(described("AI Engineer"), AREAS)
    assert backend.score == 65
    assert ai.score == 40


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("You have 3+ years of experience building APIs.", 3),
        ("5+ years of professional software engineering experience", 5),
        ("3-5 years of experience in Python", 3),
        ("At least four years of hands-on work.", 4),
        ("Minimum of 2 years in a similar role", 2),
        ("2+ years of Python and 5+ years of engineering experience", 5),
        ("Experiencia mínima de 3 años en desarrollo backend.", 3),
        ("Al menos 2 años de experiencia con Java.", 2),
        ("Minimaal 3 jaar werkervaring als developer.", 3),
        ("Mindestens 4 Jahre Berufserfahrung in Python.", 4),
        # Without the word "experience", as many postings write it.
        ("6+ years in engineering across a wide range of products", 6),
        ("You have 10+ years in ML/AI with 3+ years at Staff+ level", 10),
        ("5+ years as an ML engineer, with production models shipped", 5),
        ("4+ years building and running production software;", 4),
        ("Significant experience (typically 8+ years, or equivalent)", 8),
        ("Over 5 years of experience with distributed systems.", 5),
        # Years that are not asked of the candidate.
        ("Your data is kept for up to 2 years in our candidate pool.", None),
        ("In less than 5 years, we have grown to 480 people.", None),
        ("An onboarding period of 2 years in Madrid.", None),
        ("In your first year, you will own a service.", None),
        ("Founded 25 years ago, we have experience in retail.", None),
        ("Several years of experience in sales.", None),
        ("", None),
    ],
)
def test_years_are_read_from_the_description(text: str, years: int | None) -> None:
    assert years_required(text) == years


def test_the_description_beats_the_title() -> None:
    plain = offer("AI Engineer").model_copy(
        update={"description": "Requires 4+ years of experience."}
    )
    junior = offer("Junior AI Engineer").model_copy(
        update={"description": "You have 1 year of experience with Python."}
    )
    asked = assess_fit(plain, AREAS)
    assert asked.reasons[0] == "The description asks for 4+ years; you have only 0 in ai"
    assert asked.level is FitLevel.UNLIKELY
    assert assess_fit(junior, AREAS).reasons[0].startswith("The description asks for 1+ years")


def test_a_senior_title_is_not_lowered_by_a_short_description() -> None:
    fit = assess_fit(offer("Senior Backend Engineer", description="3+ years of experience."), AREAS)
    assert fit.reasons[0] == (
        "A senior title, usually 5+ years, though the description asks for 3+;"
        " you have 3 in backend"
    )
    assert fit.level is FitLevel.POSSIBLE


def test_an_alert_without_years_is_at_best_possible() -> None:
    fit = assess_fit(offer("AI Engineer (LLM)", location="Amsterdam"), PROFILE)
    assert fit.level is FitLevel.POSSIBLE
    assert fit.score == 60
    assert "Years asked unknown: the alert has no description, open it" in fit.reasons
    # A seniority word or a description is evidence enough.
    assert assess_fit(offer("Junior AI Engineer (LLM)"), PROFILE).level is FitLevel.GOOD
    assert assess_fit(described("AI Engineer (LLM)"), PROFILE).level is FitLevel.GOOD


def test_a_title_naming_none_of_the_roles_ranks_low() -> None:
    engineers = PROFILE.model_copy(update={"roles": ("engineer", "developer")})
    agent = assess_fit(described("Junior Customer Service Agent"), engineers)
    assert agent.level is FitLevel.UNLIKELY
    assert "The title names none of your roles (engineer, developer)" in agent.reasons
    assert assess_fit(described("Junior Backend Developer"), engineers).level is FitLevel.GOOD

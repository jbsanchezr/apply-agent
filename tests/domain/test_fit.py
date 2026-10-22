import pytest

from apply_agent.domain.fit import FitLevel, Seniority, assess_fit, seniority


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


def test_a_graduate_fits_entry_roles_and_not_senior_ones() -> None:
    junior = assess_fit("Junior Backend Developer", years=0, keywords=())
    unstated = assess_fit("AI Engineer", years=0, keywords=())
    senior = assess_fit("Senior ML Engineer", years=0, keywords=())

    assert junior.level is FitLevel.GOOD
    assert unstated.level is FitLevel.POSSIBLE
    assert senior.level is FitLevel.UNLIKELY
    assert junior.score > unstated.score > senior.score
    assert senior.reasons == ("A senior title, usually 5+ years",)


def test_experience_closes_the_gap() -> None:
    assert assess_fit("Senior ML Engineer", years=6, keywords=()).level is FitLevel.GOOD
    assert assess_fit("Senior ML Engineer", years=3, keywords=()).level is FitLevel.POSSIBLE


def test_profile_keywords_raise_the_score_as_whole_words() -> None:
    plain = assess_fit("AI Engineer", years=0, keywords=())
    matched = assess_fit("AI Engineer (LLM)", years=0, keywords=("ai", "llm", "python"))
    no_partial = assess_fit("Email Engineer", years=0, keywords=("ai",))

    assert matched.score == plain.score + 20
    assert matched.level is FitLevel.GOOD
    assert "Matches ai, llm" in matched.reasons
    assert no_partial.score == plain.score


def test_keywords_cannot_rescue_a_senior_role_for_a_graduate() -> None:
    fit = assess_fit("Senior AI Engineer (LLM)", years=0, keywords=("ai", "llm"))
    assert fit.level is FitLevel.UNLIKELY

"""How well an offer fits the candidate's profile (D58, D59).

An alert gives a title, a company and a place, never the requirements, so
"asks for four years in AI" cannot be read. The title's seniority words are
the best evidence there is: "Senior", "Lead" or "Principal" usually mean more
years, "Junior" or "Graduate" an entry role. A title with none is unknown,
not a match. Then the profile's words: roles wanted raise the score, roles to
avoid sink it, and a preferred place or a bonus word add a little.
"""

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from apply_agent.domain.keys import role_key
from apply_agent.domain.offers import JobOffer
from apply_agent.domain.profile import CandidateProfile
from apply_agent.domain.requirements import years_required


class Seniority(IntEnum):
    """Roughly the years of experience a title with these words asks for."""

    UNSTATED = -1  # no seniority word: judged on its own, not by years
    ENTRY = 0
    MID = 2
    SENIOR = 5
    LEAD = 7


class FitLevel(StrEnum):
    GOOD = "good"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"


# Matched against the role key: lower case, no accents, no punctuation.
_SENIORITY: Final = (
    (
        Seniority.LEAD,
        # "Member of Technical Staff" is a title at any level, not a staff role.
        re.compile(
            r"\b(lead|(?<!technical )staff|principal|head|director|architect|manager"
            r"|vp|chief|cto)\b"
        ),
    ),
    (Seniority.SENIOR, re.compile(r"\b(senior|sr|snr|expert|iii|experienced)\b")),
    (Seniority.MID, re.compile(r"\b(mid|middle|intermediate|ii|semi senior|ssr|medior)\b")),
    (
        Seniority.ENTRY,
        re.compile(
            r"\b(junior|jr|graduate|grad|trainee|intern|internship|entry level|apprentice"
            r"|becario|becaria|practicas|recien titulad[oa]|new grad|early career)\b"
        ),
    ),
)
_LABELS: Final = {
    Seniority.ENTRY: "an entry-level title",
    Seniority.MID: "a mid-level title",
    Seniority.SENIOR: "a senior title, usually 5+ years",
    Seniority.LEAD: "a lead or architect title, usually 7+ years",
}


@dataclass(frozen=True, slots=True)
class OfferFit:
    level: FitLevel
    score: int
    reasons: tuple[str, ...]


def seniority(title: str) -> Seniority:
    key = role_key(title)
    for level, pattern in _SENIORITY:
        if pattern.search(key):
            return level
    return Seniority.UNSTATED


def found_words(words: tuple[str, ...], *texts: str | None) -> list[str]:
    """The profile words that appear, as whole words, in any of the texts."""
    padded = " ".join(f" {role_key(t)} " for t in texts if t)
    return [w for w in words if (key := role_key(w)) and f" {key} " in padded]


def _years_had(title: str, profile: CandidateProfile) -> tuple[int, str]:
    """Years in the title's field, and the field's name for the reasons.

    A title in several fields ("AI Backend Engineer") takes the one with the
    fewest years: the role asks for experience in each field it names.
    """
    matched = [
        (area.years, name) for name, area in profile.areas.items() if found_words(area.words, title)
    ]
    if not matched:
        return profile.years_experience, "total"
    return min(matched)


def _experience(offer: JobOffer, profile: CandidateProfile) -> tuple[int, str]:
    title = offer.title
    level = seniority(title)
    # What the description says beats what the title suggests (D62).
    written = years_required(offer.description)
    stated = written is not None or level is not Seniority.UNSTATED
    if written is not None:
        asked, label = written, f"The description asks for {written}+ years"
    elif stated:
        asked, label = int(level), _LABELS[level].capitalize()
    else:
        asked = profile.unstated_years_asked
        label = f"No seniority in the title, assumed ~{asked} years"
    if asked > profile.years_ceiling:
        return 10, f"{label}, beyond your limit of {profile.years_ceiling}"
    had, area = _years_had(title, profile)
    if asked <= had:
        return (75 if stated else 65), f"{label}; you have {had} in {area}"
    if asked - had <= 2:
        return 40, f"{label}; you have {had} in {area}"
    return 10, f"{label}; you have only {had} in {area}"


def assess_fit(offer: JobOffer, profile: CandidateProfile) -> OfferFit:
    """A score from 0 to 100 from the title's seniority and the profile's words.

    An avoided word ("DevOps", "PhD", "freelance") caps the score low, whatever
    else matches: the candidate said they do not want those roles.
    """
    score, reason = _experience(offer, profile)
    reasons = [reason]
    if target := found_words(profile.target, offer.title):
        score += 10 if len(target) == 1 else 15
        reasons.append("Wanted: " + ", ".join(target))
    if place := found_words(profile.preferred_places, offer.location):
        score += 10
        reasons.append("Preferred place: " + ", ".join(place))
    if bonus := found_words(profile.bonus, offer.title, offer.company):
        score += 10
        reasons.append("Bonus: " + ", ".join(bonus))
    if avoid := found_words(profile.avoid, offer.title):
        score = min(score, 15)
        reasons.append("Avoided: " + ", ".join(avoid))
    score = max(0, min(score, 100))
    if score >= 70:
        level = FitLevel.GOOD
    elif score >= 40:
        level = FitLevel.POSSIBLE
    else:
        level = FitLevel.UNLIKELY
    return OfferFit(level, score, tuple(reasons))

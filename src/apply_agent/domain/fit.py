"""How well an offer fits the candidate's profile (D58, D59).

An alert gives a title, a company and a place, never the requirements, so
"asks for four years in AI" cannot be read. The title's seniority words are
the best evidence there is: "Senior", "Lead" or "Principal" usually mean more
years, "Junior" or "Graduate" an entry role. A title with none is unknown,
not a match. Then the profile's words: roles wanted raise the score, roles to
avoid sink it, and a preferred place or a bonus word add a little.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from apply_agent.domain.keys import role_key
from apply_agent.domain.languages import (
    language_needs,
    missing,
    title_language,
    unmet,
    written_in,
)
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


LANGUAGE_CAP: Final = 15
ASKED_BEFORE_CAP: Final = 35


def _spoken(profile: CandidateProfile) -> str:
    return ", ".join(language.capitalize() for language in profile.languages)


def _languages(
    offer: JobOffer, profile: CandidateProfile, asked_before: Sequence[str]
) -> tuple[int | None, list[str]]:
    """A cap on the score for languages the candidate lacks, and why (D69).

    Required by the posting, or the language it is written in: capped as an
    avoided word is. Asked for by the same company before, as the user noted:
    capped less, since this posting may not. A language that is a plus is only
    noted.
    """
    if not profile.languages:
        return None, []
    needs = language_needs(f"{offer.title}\n{offer.description or ''}")
    written = written_in(offer.description) or title_language(offer.title)
    required = unmet(needs, profile.languages) + ([written] if written else [])
    lacking = missing(required, profile.languages)
    cap: int | None = None
    reasons = []
    if lacking:
        cap = LANGUAGE_CAP
        named = ", ".join(language.capitalize() for language in lacking)
        how = "is written in" if lacking == [written] else "asks for"
        reasons.append(f"The posting {how} {named}; you speak {_spoken(profile)}")
    if plus := missing([n.language for n in needs if not n.required], profile.languages):
        reasons.append(", ".join(language.capitalize() for language in plus) + " is a plus")
    before = [
        language for language in missing(asked_before, profile.languages) if language not in lacking
    ]
    if before:
        cap = cap if cap is not None else ASKED_BEFORE_CAP
        named = ", ".join(language.capitalize() for language in before)
        reasons.append(f"This company asked for {named} before")
    return cap, reasons


def assess_fit(
    offer: JobOffer, profile: CandidateProfile, *, asked_before: Sequence[str] = ()
) -> OfferFit:
    """A score from 0 to 100 from the title's seniority and the profile's words.

    An avoided word ("DevOps", "PhD", "freelance") caps the score low, whatever
    else matches: the candidate said they do not want those roles. So does a
    language the job needs and the candidate does not speak; ``asked_before``
    are languages the user noted this company asking for (D69).
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
    cap, language_reasons = _languages(offer, profile, asked_before)
    if cap is not None:
        score = min(score, cap)
    reasons.extend(language_reasons)
    score = max(0, min(score, 100))
    if score >= 70:
        level = FitLevel.GOOD
    elif score >= 40:
        level = FitLevel.POSSIBLE
    else:
        level = FitLevel.UNLIKELY
    return OfferFit(level, score, tuple(reasons))

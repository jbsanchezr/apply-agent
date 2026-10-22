"""How well an offer fits the user, judged from its title alone (D58).

An alert gives a title, a company and a place, never the requirements, so
"asks for four years in AI" cannot be read. The title's seniority words are
the best evidence there is: "Senior", "Lead" or "Principal" usually mean years
the user may not have, "Junior" or "Graduate" mean an entry role. A title
with none is unknown, not a match. Keywords from the user's profile that the
title contains raise the score. It ranks offers; it never hides one.
"""

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from apply_agent.domain.keys import role_key


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


def assess_fit(title: str, *, years: int, keywords: tuple[str, ...]) -> OfferFit:
    """A score from 0 to 100: experience asked against experience had, plus keywords."""
    level = seniority(title)
    reasons = [] if level is Seniority.UNSTATED else [_LABELS[level].capitalize()]
    if level is Seniority.UNSTATED:
        score = 60
        reasons.append("Title does not say the seniority")
    elif level - years <= 0:
        score = 80
    elif level - years <= 2:
        score = 45
    else:
        score = 10
    padded = f" {role_key(title)} "
    matched = [k for k in keywords if (key := role_key(k)) and f" {key} " in padded]
    if matched:
        score = min(100, score + 10 * min(len(matched), 2))
        reasons.append("Matches " + ", ".join(matched))
    if score >= 70:
        fit = FitLevel.GOOD
    elif score >= 40:
        fit = FitLevel.POSSIBLE
    else:
        fit = FitLevel.UNLIKELY
    return OfferFit(fit, score, tuple(reasons))

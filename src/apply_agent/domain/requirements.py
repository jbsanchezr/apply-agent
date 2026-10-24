"""Years of experience a job description asks for, read with rules (D62).

LinkedIn alerts carry no description, so for them the years are guessed from
the title (D60). Company boards and Adzuna give the text, and the text says it:
"3+ years of experience", "at least five years", "3-5 years of professional
software engineering experience", "2 años de experiencia", "3 jaar ervaring".

When a description names several numbers, the largest counts: "2+ years of
Python and 5+ years of engineering experience" asks for five. A range counts
by its lower end. Numbers over 15 are ignored (they are rarely years asked).
"""

import re
from typing import Final

_WORDS: Final = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6,
}  # fmt: skip
_NUMBER: Final = r"(\d{1,2}|" + "|".join(_WORDS) + r")"
# A number, an optional plus sign or upper end of a range (hyphen, en dash,
# "to", "a", "tot"), then the word for years.
_DASHES: Final = "-" + chr(0x2013)
_YEARS: Final = _NUMBER + r"\s*\+?\s*(?:(?:[" + _DASHES + r"]|to|a|tot)\s*\d{1,2}\s*\+?\s*)?"
# The words after "years" are a lookahead, so they are not consumed and a
# second number in the same sentence is found too.
_PATTERNS: Final = (
    # English: N+ years of (some words) experience.
    re.compile(_YEARS + r"(?:years?|yrs?)\b(?=[^.;\n]{0,60}?\bexperien)", re.IGNORECASE),
    # English: at least / minimum (of) N years; experience of N years.
    re.compile(
        r"\b(?:at least|minimum(?: of)?|min\.?|experience of)\s+" + _YEARS + r"(?:years?|yrs?)\b",
        re.IGNORECASE,
    ),
    # Spanish: N años de experiencia; experiencia (mínima) de (al menos) N años.
    re.compile(_YEARS + r"años?\b(?=[^.;\n]{0,40}?\bexperiencia)", re.IGNORECASE),
    re.compile(
        r"\bexperiencia[^.;\n]{0,40}?\b(?:de|al menos|m[ií]nim[oa] de)\s+" + _YEARS + r"años?",
        re.IGNORECASE,
    ),
    # Dutch: N jaar (relevante) werkervaring; minimaal N jaar.
    re.compile(_YEARS + r"jaar\b(?=[^.;\n]{0,40}?ervaring)", re.IGNORECASE),
    re.compile(r"\bminimaal\s+" + _YEARS + r"jaar\b", re.IGNORECASE),
)
_MAX_YEARS: Final = 15


def _value(token: str) -> int:
    return int(token) if token.isdigit() else _WORDS[token.casefold()]


def years_required(description: str | None) -> int | None:
    """The most years the description asks for, or None if it names none."""
    if not description:
        return None
    found = [
        years
        for pattern in _PATTERNS
        for match in pattern.finditer(description)
        if 0 < (years := _value(match.group(1))) <= _MAX_YEARS
    ]
    return max(found, default=None)

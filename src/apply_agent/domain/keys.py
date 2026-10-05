"""Normalised keys used to recognise the same application across threads.

Companies often reply from a different thread than the one the application
started in (ATS confirmation, recruiter's personal mailbox, scheduling tool),
so thread id alone cannot identify an application. ``company_key`` and
``role_key`` give the storage layer a fallback identity that tolerates
cosmetic differences such as case, punctuation and legal suffixes.
"""

import re
import unicodedata
from typing import Final

_LEGAL_SUFFIXES: Final = frozenset(
    {"inc", "llc", "ltd", "limited", "plc", "gmbh", "ag", "bv", "sa", "sl", "sas", "srl", "corp"}
)
_NON_ALNUM: Final = re.compile(r"[^0-9a-z]+")


def _tokens(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Drop dots so abbreviations stay one token ("S.L." -> "sl"), and keep "&"
    # as a word because it is meaningful in names like "Lumen & Vale".
    cleaned = ascii_only.replace(".", "").replace("&", " and ")
    return _NON_ALNUM.sub(" ", cleaned).split()


def company_key(company: str) -> str:
    """'Kestrel Robotics, Inc.' and 'kestrel robotics' map to the same key."""
    tokens = _tokens(company)
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def similar_company(key: str, other: str) -> bool:
    """Whether two company keys could be the same company written two ways.

    True for equal keys and when one is the other plus trailing words
    ("cinderpeak" and "cinderpeak games"). It only narrows down what to offer
    the user as a possible duplicate; nothing is merged on its strength.
    """
    return key == other or key.startswith(other + " ") or other.startswith(key + " ")


def role_key(role: str | None) -> str:
    """Empty string when the role is unknown, so it still takes part in uniqueness."""
    return "" if role is None else " ".join(_tokens(role))

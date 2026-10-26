"""Which languages a job asks for, read with rules (D69).

A posting asks for a language when a sentence names it with a requirement
("fluent in German", "vloeiend Nederlands", "verhandlungssicheres Deutsch",
"Dutch only"), or when the posting itself is written in it. It is only a
plus when the sentence says so ("Dutch is a plus", "Deutsch von Vorteil").
A language named with neither ("translated into German") says nothing.

Text in parentheses is left out when judging whether a language is a plus,
so "Fluent in English and German (any other language is a plus)" still asks
for German.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from apply_agent.domain.keys import role_key

# Language (in English) -> how postings name it, in the languages they are written in.
LANGUAGES: Final[dict[str, tuple[str, ...]]] = {
    "english": ("english", "engels", "englisch", "ingles", "anglais", "engelska"),
    "spanish": ("spanish", "espanol", "castellano", "spaans", "spanisch", "espagnol", "spanska"),
    "dutch": (
        "dutch", "nederlands", "nederlandse", "nederlandstalig", "nederlandstalige",
        "niederlandisch", "neerlandes", "holandes", "neerlandais", "flemish", "vlaams",
    ),
    "german": (
        "german", "deutsch", "deutsche", "deutschen", "deutschkenntnisse", "deutschsprachig",
        "deutschsprachige", "duits", "aleman", "allemand", "tyska",
    ),
    "french": ("french", "francais", "frances", "frans", "franzosisch", "franska"),
    "swedish": ("swedish", "svenska", "sueco", "zweeds", "schwedisch", "suedois"),
    "italian": ("italian", "italiano", "italiaans", "italienisch", "italien"),
    "portuguese": ("portuguese", "portugues", "portugees", "portugiesisch"),
    "polish": ("polish", "polski", "polaco", "pools", "polnisch"),
    "danish": ("danish", "dansk", "danes", "deens", "danisch"),
    "norwegian": ("norwegian", "norsk", "noruego", "noors", "norwegisch"),
    "finnish": ("finnish", "suomi", "fines", "fins", "finnisch"),
    "czech": ("czech", "cestina", "checo", "tsjechisch", "tschechisch"),
    "japanese": ("japanese", "japones", "japans", "japanisch"),
    "mandarin": ("mandarin", "chinese", "chino", "chinees", "chinesisch"),
}  # fmt: skip

_REQUIRED: Final = re.compile(
    r"\b(fluent|fluency|fluently|native|proficien\w*|business[ -]?(level|fluent)|"
    r"c1|c2|b2|required|requirement|must|mandatory|essential|speak\w*|spoken|"
    r"excellent|strong command|command of|working knowledge|good knowledge|only|"
    r"vloeiend\w*|beheers\w*|spreek\w*|schrijf\w*|uitstekend\w*|"
    r"flie(ss|ß)end\w*|verhandlungssicher\w*|sehr gute\w*|gute\w*|kenntnisse|"
    r"courant|parfait\w*|maitrise|fluido|nativo|imprescindible|requisito|dominio|"
    r"flytande|obligatorisk\w*)\b",
    re.IGNORECASE,
)
_OPTIONAL: Final = re.compile(
    r"\b(a plus|plus|nice to have|bonus|advantage\w*|preferred|preferably|desirable|"
    r"beneficial|asset|ideally|pre|pluspunt|von vorteil|wunschenswert|"
    r"valorable|se valorara|deseable|meriterande|un atout|apprecie)\b",
    re.IGNORECASE,
)
_SENTENCES: Final = re.compile(r"[.;\n•·]|\s-\s|\s\*\s")
_PARENTHESES: Final = re.compile(r"\([^)]*\)")

# Words common in one language's prose and rare in the others'.
_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "english": frozenset(
        ("the", "and", "to", "of", "in", "for", "with", "you", "we", "our", "is", "are",
         "will", "your", "this")
    ),
    "spanish": frozenset(
        ("el", "la", "los", "las", "y", "que", "en", "por", "con", "para", "una", "es",
         "su", "nuestro", "del")
    ),
    "dutch": frozenset(
        ("het", "een", "en", "van", "is", "dat", "op", "voor", "met", "zijn", "je", "wij",
         "jij", "ons", "onze", "bij", "ook", "naar")
    ),
    "german": frozenset(
        ("der", "die", "das", "und", "ist", "nicht", "mit", "fur", "auf", "wir", "sie",
         "ihre", "unser", "du", "dein", "bei", "auch", "werden")
    ),
    "swedish": frozenset(
        ("och", "att", "det", "som", "ar", "for", "med", "pa", "av", "vi", "du", "din",
         "var", "till", "har")
    ),
    "french": frozenset(
        ("le", "les", "et", "des", "est", "pour", "avec", "nous", "vous", "une", "dans",
         "sur", "au", "aux")
    ),
}  # fmt: skip
MIN_WORDS_TO_JUDGE: Final = 60
MIN_STOPWORD_SHARE: Final = 0.06
# Job titles written in a language: a word for the job, not the language's name.
_TITLE_WORDS: Final = {
    "dutch": ("ontwikkelaar", "medewerker", "adviseur", "beheerder", "stagiair", "vacature"),
    "swedish": ("utvecklare", "systemutvecklare", "konsult", "ingenjor"),
    "german": ("entwickler", "mitarbeiter", "berater", "sachbearbeiter", "werkstudent"),
}

_ALIASES: Final = {alias: name for name, aliases in LANGUAGES.items() for alias in aliases}


_OR_WORDS: Final = frozenset({"or", "o", "u", "oder", "ou", "eller"})
_AND_WORDS: Final = frozenset({"and", "y", "e", "und", "et", "och"})
_CHOICE: Final = ("at least one", "one of", "any of", "minstens een", "mindestens eine")


def _alternatives(words: Sequence[str]) -> list[frozenset[str]]:
    """Languages offered as a choice: 'french and or spanish', or a list after 'one of'.

    A run of languages joined by "or" is a choice; "English and Dutch" is not.
    After "at least one" or "one of", languages listed together are a choice,
    so "English and at least one Nordic language (Finnish, Danish)" makes
    Finnish and Danish a choice and English a need of its own.
    """
    text = " ".join(words)
    choice_phrase = any(phrase in text for phrase in _CHOICE)
    groups: list[frozenset[str]] = []
    run: list[str] = []
    joined_by_or = False
    for word in [*words, ""]:
        if word in _ALIASES:
            run.append(_ALIASES[word])
        elif run and word in _OR_WORDS:
            joined_by_or = True
        elif run and word in _AND_WORDS:
            continue
        else:
            languages = frozenset(run)
            if len(languages) > 1 and (joined_by_or or choice_phrase):
                groups.append(languages)
            run, joined_by_or = [], False
    return groups


@dataclass(frozen=True, slots=True)
class LanguageNeed:
    language: str
    required: bool
    evidence: str
    # The languages the sentence offers as alternatives ("French or Spanish"),
    # this one included: speaking any of them meets the need. Empty: no choice.
    either: frozenset[str] = frozenset()


def language_named(text: str) -> str | None:
    """The language a word or phrase names ('Nederlands' is 'dutch'), or None."""
    for word in role_key(text).split():
        if word in _ALIASES:
            return _ALIASES[word]
    return None


def language_needs(text: str | None) -> list[LanguageNeed]:
    """The languages the text asks for, required or a plus, one entry each."""
    if not text:
        return []
    found: dict[str, LanguageNeed] = {}
    sentences = _SENTENCES.split(text)
    # A parenthesis is also judged on its own: "(Dutch only)" asks for Dutch.
    sentences += [p.strip("()") for s in sentences for p in _PARENTHESES.findall(s)]
    for sentence in sentences:
        words = role_key(sentence).split()
        named = {_ALIASES[w] for w in words if w in _ALIASES}
        if not named:
            continue
        plain = role_key(_PARENTHESES.sub(" ", sentence))
        required = bool(_REQUIRED.search(plain))
        optional = bool(_OPTIONAL.search(plain))
        if not (required or optional):
            continue
        evidence = " ".join(sentence.split())[:160]
        groups = _alternatives(words)
        for language in sorted(named):
            either = next((g for g in groups if language in g), frozenset())
            # Inside parentheses only, the sentence's judgement is not about it,
            # unless the parentheses list the choice ("one Nordic language (...)").
            in_sentence = any(alias in plain.split() for alias in LANGUAGES[language])
            if not in_sentence and not either:
                continue
            need = LanguageNeed(language, required and not optional, evidence, either)
            known = found.get(language)
            if known is None or (need.required and not known.required):
                found[language] = need
    return list(found.values())


def written_in(text: str | None) -> str | None:
    """The language a description is written in, if it is long enough to tell."""
    words = role_key(text or "").split()
    if len(words) < MIN_WORDS_TO_JUDGE:
        return None
    shares = {
        language: sum(1 for w in words if w in stopwords) / len(words)
        for language, stopwords in _STOPWORDS.items()
    }
    language, share = max(shares.items(), key=lambda item: item[1])
    return language if share >= MIN_STOPWORD_SHARE else None


def title_language(title: str) -> str | None:
    """The language a job title is written in, when a word for the job gives it away."""
    words = role_key(title).split()
    for language, vocabulary in _TITLE_WORDS.items():
        if any(word.startswith(stem) for word in words for stem in vocabulary):
            return language
    return None


def missing(needs: Sequence[str], spoken: Sequence[str]) -> list[str]:
    """The needed languages the candidate does not speak, in order, once each."""
    have = {language_named(s) or s.casefold() for s in spoken}
    return [n for n in dict.fromkeys(needs) if n not in have]


def unmet(needs: Sequence[LanguageNeed], spoken: Sequence[str]) -> list[str]:
    """Required languages the candidate does not speak, alternatives considered."""
    have = {language_named(s) or s.casefold() for s in spoken}
    lacking = [
        need.language
        for need in needs
        if need.required and need.language not in have and not (need.either & have)
    ]
    return list(dict.fromkeys(lacking))


# The language a job in a country most likely asks for besides English, to
# suggest when the user notes one.
COUNTRY_LANGUAGE: Final = {
    "Netherlands": "dutch",
    "Belgium": "dutch",
    "Germany": "german",
    "Austria": "german",
    "Switzerland": "german",
    "Sweden": "swedish",
    "France": "french",
    "Italy": "italian",
    "Denmark": "danish",
    "Poland": "polish",
    "Portugal": "portuguese",
    "Czech Republic": "czech",
    "Japan": "japanese",
}

"""Offers from a LinkedIn search page the user copied and pasted (D73).

The user runs the search in their own browser, signed in, selects the page
(Ctrl+A, Ctrl+C) and pastes it into the app. The browser puts the page's
HTML on the clipboard, links included, so each job card gives its posting's
id (``/jobs/view/123`` or ``currentJobId=123``) and its lines of text: the
title, the company, the place, then hints ("Promoted", "Easy Apply", "1
week ago"). Nothing is fetched from LinkedIn; the app only reads what the
user saw.

LinkedIn's markup changes and is not documented, so this reads structure
loosely: a card is the innermost list item holding one posting's links, or,
without list items, the text between one posting's link and the next. What
it reads is shown to the user before anything is saved.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Final

from pydantic import ValidationError

from apply_agent.domain.offers import JobOffer, OfferSource
from apply_agent.offers.alerts import is_hint, posting_url

_JOB_LINK: Final = re.compile(r"(?:/jobs/view/(?:[^/?#\s]*?-)?|[?&]currentJobId=)(\d{6,20})")
# Tags that end a line of text.
_BLOCKS: Final = frozenset(
    {"br", "div", "p", "li", "ul", "ol", "span", "h1", "h2", "h3", "h4", "a", "time", "strong"}
)
_SKIPPED: Final = frozenset({"script", "style", "svg", "button", "noscript", "template"})
# Card lines that say something about the posting, not what it is.
_NOISE: Final = re.compile(
    r"^(viewed|visto|saved|guardado|applied|solicitado|new|nuevo|dismiss|descartar|verified"
    r"|verificad[oa]|be an early applicant|·|\d+ (?:of|de) \d+)$"
    r"|^(?:reposted |publicado de nuevo )?(?:\d+|an?|one) (?:minutes?|hours?|days?|weeks?"
    r"|months?) ago$"
    r"|^(?:publicado de nuevo )?hace \d+ \w+$"
    r"|^\d[\d.,]*\+?\s*(?:results?|resultados?)$",
    re.IGNORECASE,
)
_VERIFIED: Final = re.compile(r"\s+(?:with|con) verificaci[oó]n$|\s+with verification$", re.I)
MAX_OFFERS: Final = 200


@dataclass
class _Card:
    job_ids: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    posted: str | None = None


class _PageParser(HTMLParser):
    """Text lines and posting ids, grouped by list item (or by link without one)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[_Card] = []
        self._items: list[_Card] = []  # open <li> elements, innermost last
        self._loose = _Card()  # text outside any list item
        self._line: list[str] = []
        self._skipping = 0

    def _current(self) -> _Card:
        return self._items[-1] if self._items else self._loose

    def _end_line(self) -> None:
        text = " ".join("".join(self._line).split())
        self._line = []
        if text:
            self._current().lines.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
            return
        if tag in _BLOCKS:
            self._end_line()
        if tag == "li":
            self._items.append(_Card())
            return
        attributes = {name: value or "" for name, value in attrs}
        if tag == "a" and (match := _JOB_LINK.search(attributes.get("href", ""))):
            card = self._current()
            if not self._items and card.job_ids and match.group(1) not in card.job_ids:
                # Outside list items, a new posting's link starts a new card.
                self.cards.append(card)
                self._loose = card = _Card()
            if match.group(1) not in card.job_ids:
                card.job_ids.append(match.group(1))
        if tag == "time" and attributes.get("datetime"):
            self._current().posted = self._current().posted or attributes["datetime"]

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping = max(0, self._skipping - 1)
            return
        if tag in _BLOCKS:
            self._end_line()
        if tag == "li" and self._items:
            item = self._items.pop()
            if len(item.job_ids) == 1:
                self.cards.append(item)
            elif self._items and not item.job_ids:
                # A list item with no posting is part of its parent's card.
                self._items[-1].lines.extend(item.lines)

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._line.append(data)

    def close(self) -> None:
        super().close()
        self._end_line()
        if len(self._loose.job_ids) == 1:
            self.cards.append(self._loose)


def _posting_lines(lines: list[str]) -> list[str]:
    """The card's lines that describe the posting, once each, hints and noise left out."""
    kept: list[str] = []
    for line in lines:
        line = _VERIFIED.sub("", line).strip(" ·")
        if not line or is_hint(line) or _NOISE.search(line) or line in kept:
            continue
        kept.append(line)
    return kept


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class PastedPage:
    offers: tuple[JobOffer, ...]
    # Postings whose card could not be read (no company line, say).
    unreadable: int


def parse_pasted_search(html: str, seen_at: datetime) -> PastedPage:
    """The postings on a pasted LinkedIn search page, once each, in page order."""
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    offers: dict[str, JobOffer] = {}
    unreadable: set[str] = set()
    for card in parser.cards:
        [job_id] = card.job_ids
        lines = _posting_lines(card.lines)
        if job_id in offers or len(lines) < 2:
            unreadable.add(job_id)
            continue
        try:
            offers[job_id] = JobOffer(
                source=OfferSource.LINKEDIN_SEARCH,
                linkedin_id=job_id,
                title=lines[0],
                company=lines[1],
                location=lines[2] if len(lines) > 2 else None,
                url=posting_url(job_id),
                posted_on=_date(card.posted),
                seen_at=seen_at,
            )
        except ValidationError:
            unreadable.add(job_id)
        if len(offers) >= MAX_OFFERS:
            break
    return PastedPage(tuple(offers.values()), len(unreadable - offers.keys()))

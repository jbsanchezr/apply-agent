"""Job offers from LinkedIn job alert emails, read with rules, not a model.

An alert lists postings as short blocks of lines separated by blank lines or
rules of dashes. Two layouts are recognised:

    Machine Learning Engineer              Data Engineer
    Brisa Mobility · Madrid (Hybrid)       Acme Corp
    Easy Apply                             Madrid, Community of Madrid, Spain
                                           View job: https://www.linkedin.com/comm/jobs/view/4012345678/

A block counts as a posting only with a "company · location" second line or a
link to a posting, so headers, "see all jobs" links and footers are skipped.
Nothing here can change an application: offers are leads, not events.
"""

import re
from typing import Final

from pydantic import ValidationError

from apply_agent.domain import Message
from apply_agent.domain.offers import JobOffer, OfferSource

# The sender of LinkedIn's job alerts, in every language and region.
ALERT_SENDER: Final = "jobalerts-noreply@linkedin."
# Listing all LinkedIn mail is the coarse filter the provider understands; the
# alert sender is checked afterwards, here.
PROVIDER_SENDER_FILTER: Final = "linkedin"

_SEPARATOR: Final = re.compile(r"^\s*[-=_─]{3,}\s*$")
_POSTING_LINK: Final = re.compile(
    r"linkedin\.[a-z.]+/(?:comm/)?jobs/view/(?:[^/\s?]*?-)?(\d{6,20})"
)
_URL: Final = re.compile(r"https?://\S+")
_LOCATION_HINT: Final = re.compile(
    r",|\(|\b(remote|remoto|en remoto|hybrid|h[ií]brido|on-site|presencial|spain|espa[nñ]a)\b",
    re.IGNORECASE,
)
_MAX_LOCATION: Final = 120


def is_linkedin_alert(message: Message) -> bool:
    return ALERT_SENDER in message.sender.casefold()


def posting_url(linkedin_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{linkedin_id}/"


def _blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _SEPARATOR.match(line):
            blocks.append([])
        else:
            blocks[-1].append(line)
    return [b for b in blocks if b]


def _offer(block: list[str], message: Message) -> JobOffer | None:
    link = next((m for line in block if (m := _POSTING_LINK.search(line))), None)
    text = [line for line in block if not _URL.search(line)]
    if len(text) < 2:
        return None
    title, second = text[0], text[1]
    location: str | None
    if " · " in second:
        company, location = (part.strip() for part in second.split(" · ", 1))
    elif link is not None:
        company = second
        third = text[2] if len(text) > 2 else ""
        # The line after the company is a location, or a hint such as "Easy Apply".
        looks_like_place = len(third) <= _MAX_LOCATION and _LOCATION_HINT.search(third)
        location = third if third and looks_like_place else None
    else:
        return None
    linkedin_id = link.group(1) if link else None
    try:
        return JobOffer(
            source=OfferSource.LINKEDIN_ALERT,
            linkedin_id=linkedin_id,
            title=title,
            company=company,
            location=location or None,
            url=posting_url(linkedin_id) if linkedin_id else None,
            seen_at=message.sent_at,
        )
    except ValidationError:
        # An odd block (a 300-character "title") is skipped, not fatal.
        return None


def parse_linkedin_alert(message: Message) -> list[JobOffer]:
    """The postings an alert email lists, in order. Empty for any other email."""
    if not is_linkedin_alert(message):
        return []
    offers: dict[str, JobOffer] = {}
    for block in _blocks(message.body_text):
        offer = _offer(block, message)
        if offer is not None:
            offers.setdefault(offer.key, offer)
    return list(offers.values())

"""Job offers from LinkedIn job alert emails, read with rules, not a model.

Real alerts (D58) list each posting as title, company and location, then
LinkedIn's hints, then a link, with rules of dashes between postings:

    Desarrollador/a Python - Microservicios y Agentes de IA
    METRICA
    Madrid

    This company is actively hiring
    View job: https://www.linkedin.com/comm/jobs/view/4480238305/?trackingId=...

    ---------------------------------------------------------

A posting is the lines before each "View job" link, since the last link or
rule, without URLs and hints. The blank line before the hints is not a
boundary. Alerts with no posting links fall back to the older layout of
blank-line blocks with a "company · location" line:

    Machine Learning Engineer
    Brisa Mobility · Madrid (Hybrid)
    Easy Apply

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
# What LinkedIn writes under a posting about the posting, not the posting itself.
_HINT: Final = re.compile(
    r"actively (hiring|recruiting)|\balum(ni|nus|na|nae)?\b|apply with resume|easy apply"
    r"|\bapplicants?\b|\bconnections?\b|early applicant|\bpromoted\b|your profile matches"
    r"|solicitud sencilla|contratando activamente|tu perfil coincide|\bsolicitudes\b"
    r"|antiguos alumnos|\bcontactos?\b|promocionad[ao]|proceso de solicitud",
    re.IGNORECASE,
)


def is_linkedin_alert(message: Message) -> bool:
    return ALERT_SENDER in message.sender.casefold()


def is_hint(line: str) -> bool:
    """'This company is actively hiring', '143 company alumni', 'Easy Apply'..."""
    return bool(_HINT.search(line))


def posting_url(linkedin_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{linkedin_id}/"


def _offer(
    title: str, company: str, location: str | None, linkedin_id: str | None, message: Message
) -> JobOffer | None:
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
        # An odd posting (a 300-character "title") is skipped, not fatal.
        return None


def _linked_postings(lines: list[str], message: Message) -> list[JobOffer]:
    offers: list[JobOffer] = []
    pending: list[str] = []
    for line in lines:
        link = _POSTING_LINK.search(line)
        if link is not None:
            # A header line may come first, so the last three lines are the posting.
            text = [p for p in pending if not is_hint(p)][-3:]
            if len(text) >= 2:
                location = text[2] if len(text) == 3 else None
                offer = _offer(text[0], text[1], location, link.group(1), message)
                if offer is not None:
                    offers.append(offer)
            pending = []
        elif _SEPARATOR.match(line):
            pending = []
        elif line and not _URL.search(line):
            pending.append(line)
    return offers


def _dotted_postings(lines: list[str], message: Message) -> list[JobOffer]:
    blocks: list[list[str]] = [[]]
    for line in lines:
        if not line or _SEPARATOR.match(line):
            blocks.append([])
        elif not _URL.search(line):
            blocks[-1].append(line)
    offers = []
    for block in blocks:
        if len(block) >= 2 and " · " in block[1]:
            company, location = (part.strip() for part in block[1].split(" · ", 1))
            offer = _offer(block[0], company, location, None, message)
            if offer is not None:
                offers.append(offer)
    return offers


def parse_linkedin_alert(message: Message) -> list[JobOffer]:
    """The postings an alert email lists, in order. Empty for any other email."""
    if not is_linkedin_alert(message):
        return []
    lines = [line.strip() for line in message.body_text.splitlines()]
    has_links = any(_POSTING_LINK.search(line) for line in lines)
    found = _linked_postings(lines, message) if has_links else _dotted_postings(lines, message)
    offers: dict[str, JobOffer] = {}
    for offer in found:
        offers.setdefault(offer.key, offer)
    return list(offers.values())

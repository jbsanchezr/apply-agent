"""Job offers found on job boards, and whether the user has applied to them.

An offer is only a lead: it never creates or changes an application. Whether
the user has applied is worked out on every read from the applications table,
so an application recorded after the offer was found shows up at once.
"""

import hashlib
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field

from apply_agent.domain.enums import ApplicationStatus
from apply_agent.domain.keys import company_key, role_key, similar_company
from apply_agent.domain.models import Application, DomainModel, UtcDatetime

ShortStr = Annotated[str, Field(min_length=1, max_length=200)]


class OfferSource(StrEnum):
    LINKEDIN_ALERT = "linkedin_alert"
    LINKEDIN_SEARCH = "linkedin_search"


class OfferState(StrEnum):
    """What the user decided about an offer. Applying is detected, not set."""

    NEW = "new"
    INTERESTED = "interested"
    DISMISSED = "dismissed"


class OfferMatch(StrEnum):
    APPLIED = "applied"
    SAME_COMPANY = "same_company"
    NONE = "none"


class JobOffer(DomainModel):
    """One posting as a job board described it."""

    source: OfferSource
    # LinkedIn's posting id when known. It identifies the posting across alert
    # emails and searches; without it, company + role + location do.
    linkedin_id: Annotated[str, Field(pattern=r"^\d{1,20}$")] | None = None
    title: ShortStr
    company: ShortStr
    location: Annotated[str, Field(max_length=200)] | None = None
    url: Annotated[str, Field(max_length=500, pattern=r"^https://")] | None = None
    posted_on: date | None = None
    seen_at: UtcDatetime

    @property
    def key(self) -> str:
        if self.linkedin_id is not None:
            return f"linkedin:{self.linkedin_id}"
        identity = "|".join(
            (company_key(self.company), role_key(self.title), role_key(self.location))
        )
        return "posting:" + hashlib.sha256(identity.encode()).hexdigest()[:24]


class StoredOffer(DomainModel):
    id: int
    offer: JobOffer
    state: OfferState
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime
    # When the user found the posting no longer open (D61).
    closed_at: UtcDatetime | None = None


@dataclass(frozen=True, slots=True)
class OfferApplicationMatch:
    """How an offer relates to the user's applications."""

    kind: OfferMatch
    application_id: int | None = None
    application_role: str | None = None
    application_status: ApplicationStatus | None = None


def _same_role(offer_key: str, application_key: str) -> bool:
    """'senior ml engineer' and 'senior ml engineer recommendations' are one posting.

    Boards and confirmation emails often disagree on a title's tail (team,
    location, contract type), so a title that extends the other one matches.
    """
    return bool(offer_key) and similar_company(offer_key, application_key)


def match_offer(offer: JobOffer, applications: Sequence[Application]) -> OfferApplicationMatch:
    """Whether the user already applied to this offer, or to this company.

    Only a role that is known on both sides can prove an application. An
    application with no role at the same company is shown as a hint, never
    as proof, because one company often has several open roles.
    """
    ckey, rkey = company_key(offer.company), role_key(offer.title)
    same_company = [a for a in applications if similar_company(ckey, company_key(a.company))]
    for app in same_company:
        if app.role is not None and _same_role(rkey, role_key(app.role)):
            return OfferApplicationMatch(OfferMatch.APPLIED, app.id, app.role, app.status)
    if same_company:
        latest = max(same_company, key=lambda a: a.last_activity_at)
        return OfferApplicationMatch(OfferMatch.SAME_COMPANY, latest.id, latest.role, latest.status)
    return OfferApplicationMatch(OfferMatch.NONE)


# Closed offers needed before their age is trusted, and the shortest age the
# page will fold at: one quick closure must not hide a week of offers.
MIN_CLOSURES_TO_LEARN: Final = 5
MIN_LEARNED_DAYS: Final = 3


def typical_closing_age(ages_days: Sequence[int]) -> int | None:
    """How old offers usually are when the user finds them closed (D61).

    The median of the ages recorded when the user marked offers as no longer
    available, or None with fewer than ``MIN_CLOSURES_TO_LEARN`` marks. It is
    when they were found closed, not when they closed, so it errs late.
    """
    if len(ages_days) < MIN_CLOSURES_TO_LEARN:
        return None
    return max(round(statistics.median(ages_days)), MIN_LEARNED_DAYS)

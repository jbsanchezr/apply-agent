"""What the user asked the Search page to look for (D62)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import Field

from apply_agent.domain.models import DomainModel

# Countries the search form offers, by their two-letter codes. A title search
# runs on the sources that cover the country chosen (InfoJobs: Spain).
Country = Literal[
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
]  # fmt: skip
COUNTRY_NAMES: Final = {
    "at": "Austria", "au": "Australia", "be": "Belgium", "br": "Brazil", "ca": "Canada",
    "ch": "Switzerland", "de": "Germany", "es": "Spain", "fr": "France",
    "gb": "United Kingdom", "in": "India", "it": "Italy", "mx": "Mexico",
    "nl": "Netherlands", "nz": "New Zealand", "pl": "Poland", "sg": "Singapore",
    "us": "United States", "za": "South Africa",
}  # fmt: skip

Text = Annotated[str, Field(min_length=1, max_length=100)]


class JobSearch(DomainModel):
    """A title search: title or keywords, place, country, company, age."""

    what: Text
    where: Text | None = None
    country: Country = "es"
    company: Text | None = None
    max_days_old: Annotated[int, Field(ge=1, le=60)] = 14


class SavedSearch(DomainModel):
    id: int
    search: JobSearch


class WatchedCompany(DomainModel):
    """A company whose own job board is read on every sync."""

    id: int
    name: Text
    board: Text
    slug: Text
    # Only titles with one of these words are kept; empty uses the profile's.
    keywords: tuple[Text, ...] = ()
    # Only postings in one of these places are kept; empty keeps all.
    places: tuple[Text, ...] = ()
    last_checked_at: datetime | None = None
    last_error: str | None = None


class BoardStatus(StrEnum):
    """What looking for a target company's job board found (D66)."""

    PENDING = "pending"  # not looked for yet
    FOUND = "found"  # a board it surely uses; it is watched
    POSSIBLE = "possible"  # a board under its name, maybe someone else's
    NONE = "none"  # no board this app can read
    ERROR = "error"


class TargetCompany(DomainModel):
    """A company the user wants to follow, loaded from a list (D66)."""

    id: int
    name: Text
    group: Annotated[str, Field(max_length=60)] | None = None
    context: Text | None = None
    status: BoardStatus = BoardStatus.PENDING
    board: Text | None = None
    slug: Text | None = None
    # Example titles of a possible board, or the error, for the user to read.
    note: Annotated[str, Field(max_length=300)] | None = None
    checked_at: datetime | None = None

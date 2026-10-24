"""What the user asked the Search page to look for (D62)."""

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import Field

from apply_agent.domain.models import DomainModel

# Countries Adzuna's API covers, by its two-letter codes.
AdzunaCountry = Literal[
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
    """A search of Adzuna: title or keywords, place, country, company, age."""

    what: Text
    where: Text | None = None
    country: AdzunaCountry = "nl"
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

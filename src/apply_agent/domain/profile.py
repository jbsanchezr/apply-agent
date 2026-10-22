"""The candidate's profile: what they have and what they want from a job (D59).

Words are matched as whole words against the role key of a title, company or
place (lower case, no accents, no punctuation), so "back end" also matches
"Back-End" and "ia" does not match "Ingeniería".
"""

from typing import Annotated, Self

from pydantic import Field, model_validator

from apply_agent.domain.models import DomainModel

Words = tuple[Annotated[str, Field(min_length=1, max_length=60)], ...]


class CandidateProfile(DomainModel):
    years_experience: int = Field(default=0, ge=0, le=50)
    # The most years a posting may ask for before it ranks low. Defaults to
    # two more than the candidate has.
    max_years_asked: int | None = Field(default=None, ge=0, le=50)
    # Roles, skills and topics wanted in a title.
    target: Words = ()
    # Words that make a title a poor fit whatever else it says (DevOps, PhD...).
    avoid: Words = ()
    # Places that make an offer better. Anywhere else is neutral.
    preferred_places: Words = ()
    # Words in a title or company name worth a little extra (an industry...).
    bonus: Words = ()

    @model_validator(mode="after")
    def _ceiling_not_below_experience(self) -> Self:
        if self.max_years_asked is not None and self.max_years_asked < self.years_experience:
            raise ValueError("max_years_asked must not be lower than years_experience")
        return self

    @property
    def years_ceiling(self) -> int:
        return self.years_experience + 2 if self.max_years_asked is None else self.max_years_asked

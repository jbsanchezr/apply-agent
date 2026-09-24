"""Provider- and storage-agnostic domain models.

All models are immutable and reject unknown fields, so a typo or an
unexpected key coming from an LLM or a provider fails loudly at the boundary
instead of propagating silently.
"""

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from apply_agent.domain.enums import ApplicationStatus, MessageCategory


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _single_line(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("must be a single line")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""A timezone-aware datetime, normalised to UTC. Naive datetimes are rejected."""

NonEmptyStr = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Message(DomainModel):
    """An inbound email as the agent sees it, independent of the mail provider."""

    id: NonEmptyStr = Field(description="Stable provider id; the idempotency key.")
    thread_id: NonEmptyStr
    subject: str
    sender: NonEmptyStr = Field(description="Sender as 'Display Name <address>'.")
    sent_at: UtcDatetime
    body_text: str = Field(description="Plain-text body; HTML is converted by the provider.")


class Event(DomainModel):
    """The outcome of processing one message: what it says about an application."""

    message_id: NonEmptyStr
    thread_id: NonEmptyStr
    category: MessageCategory
    occurred_at: UtcDatetime
    summary: Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_single_line)]


class Application(DomainModel):
    """One job application and its current status."""

    id: int | None = None
    company: Annotated[str, Field(min_length=1, max_length=200)]
    role: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    status: ApplicationStatus
    first_seen_at: UtcDatetime
    last_activity_at: UtcDatetime
    thread_ids: frozenset[NonEmptyStr] = Field(min_length=1)

    @model_validator(mode="after")
    def _activity_not_before_first_seen(self) -> Self:
        if self.last_activity_at < self.first_seen_at:
            raise ValueError("last_activity_at must not be earlier than first_seen_at")
        return self

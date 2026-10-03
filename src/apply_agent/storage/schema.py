"""SQLite schema, expressed as SQLAlchemy 2.0 typed ORM tables.

Idempotency lives in the schema, not in application code:

* ``events.message_id`` is unique, so a message can be recorded only once.
  Every processed message gets a row, including noise (``application_id`` is
  NULL), so re-running a sync never pays for the same LLM call twice.
* ``application_threads.thread_id`` is the primary key, so a thread belongs to
  at most one application.
* ``(company_key, role_key)`` is unique, so a reply arriving in a new thread
  still resolves to the existing application.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from apply_agent.domain.enums import ApplicationStatus, MessageCategory
from apply_agent.storage.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum[E: (ApplicationStatus, MessageCategory)](enum_cls: type[E]) -> Enum:
    """Store enum *values* as VARCHAR with a CHECK constraint (portable, readable)."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UtcDateTime()}


class ApplicationRow(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("company_key", "role_key", name="uq_application_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    role_key: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[ApplicationStatus] = mapped_column(_str_enum(ApplicationStatus))
    first_seen_at: Mapped[datetime]
    last_activity_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    threads: Mapped[list["ApplicationThreadRow"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    events: Mapped[list["EventRow"]] = relationship(back_populates="application")
    override: Mapped["StatusOverrideRow | None"] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class ApplicationThreadRow(Base):
    __tablename__ = "application_threads"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )

    application: Mapped[ApplicationRow] = relationship(back_populates="threads")


class EventRow(Base):
    """One row per processed message. Raw bodies are deliberately not stored."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_application_occurred", "application_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True)
    thread_id: Mapped[str] = mapped_column(String(255), index=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL")
    )
    category: Mapped[MessageCategory] = mapped_column(_str_enum(MessageCategory))
    occurred_at: Mapped[datetime]
    summary: Mapped[str] = mapped_column(String(200))
    processed_at: Mapped[datetime] = mapped_column(default=_utcnow)

    application: Mapped[ApplicationRow | None] = relationship(back_populates="events")


class StatusOverrideRow(Base):
    """A status the user set by hand (see D51). At most one per application.

    A separate table rather than a column, so databases created before it
    existed pick it up from ``create_all`` with no migration (D9).
    """

    __tablename__ = "status_overrides"

    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(_str_enum(ApplicationStatus))
    set_at: Mapped[datetime]

    application: Mapped[ApplicationRow] = relationship(back_populates="override")


class FollowUpDraftRow(Base):
    """A follow-up the agent suggests. Stored here only; never sent anywhere."""

    __tablename__ = "follow_up_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

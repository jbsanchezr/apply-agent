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

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from apply_agent.domain.enums import ApplicationStatus, MessageCategory
from apply_agent.domain.offers import OfferSource, OfferState
from apply_agent.storage.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum[E: (ApplicationStatus, MessageCategory, OfferSource, OfferState)](
    enum_cls: type[E],
) -> Enum:
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


class ApplicationAliasRow(Base):
    """Another (company, role) spelling the user said is this application (D54).

    Written when two applications are merged, so the next email that uses the
    merged-away spelling joins the surviving application instead of creating
    the duplicate again.
    """

    __tablename__ = "application_aliases"

    company_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    role_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )


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


class JobOfferRow(Base):
    """A posting found on a job board (see D57). Never linked to an application.

    Whether the user applied is worked out when offers are read, from the
    applications table, so it cannot go stale.
    """

    __tablename__ = "job_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # LinkedIn's posting id, or a hash of company, role and location (JobOffer.key).
    offer_key: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[OfferSource] = mapped_column(_str_enum(OfferSource))
    linkedin_id: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    posted_on: Mapped[date | None]
    state: Mapped[OfferState] = mapped_column(_str_enum(OfferState), default=OfferState.NEW)
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    # Added in D62; a database from before gets them from migrate().
    external_id: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)

    closure: Mapped["OfferClosureRow | None"] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class ScannedAlertRow(Base):
    """An email the offer scanner has read, so it is not downloaded again.

    Separate from ``events``: the agent and the scanner read the inbox for
    different purposes, and either may run without the other.
    """

    __tablename__ = "scanned_alert_messages"

    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    sent_at: Mapped[datetime]
    offers_found: Mapped[int] = mapped_column(default=0)


class OfferClosureRow(Base):
    """The user found this offer no longer open (D61). At most one per offer.

    A table rather than a new ``state`` value: the state column's CHECK
    constraint is fixed when the table is created, and databases created
    before this existed pick a new table up from ``create_all`` (D9).
    """

    __tablename__ = "offer_closures"

    offer_id: Mapped[int] = mapped_column(
        ForeignKey("job_offers.id", ondelete="CASCADE"), primary_key=True
    )
    reported_at: Mapped[datetime]
    # Days from the last alert that listed it to the day it was found closed.
    age_days: Mapped[int]
    # True when a company board stopped listing it (D62), false when the user
    # reported it. Only reports teach the fold age: a board closure is seen
    # at once, so its age says nothing about when users find offers closed.
    detected: Mapped[bool] = mapped_column(default=False)

    offer: Mapped[JobOfferRow] = relationship(back_populates="closure")


class SavedSearchRow(Base):
    """A search the user saved on the Search page, run again on every sync (D62)."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    what: Mapped[str] = mapped_column(String(100))
    where: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2))
    company: Mapped[str | None] = mapped_column(String(100))
    max_days_old: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class WatchedCompanyRow(Base):
    """A company whose own job board is read on every sync (D62).

    ``board`` and ``slug`` say where it was found (Greenhouse, Lever, Ashby);
    plain strings rather than an enum, so a new board needs no migration.
    """

    __tablename__ = "watched_companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    board: Mapped[str] = mapped_column(String(20))
    slug: Mapped[str] = mapped_column(String(100))
    # Only titles with one of these words are kept; empty uses the profile's.
    keywords: Mapped[str | None] = mapped_column(String(300))
    # Only postings in one of these places are kept; empty keeps all.
    places: Mapped[str | None] = mapped_column(String(300))
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (UniqueConstraint("board", "slug", name="uq_watched_board"),)


class TargetCompanyRow(Base):
    """A company on the user's list (D66): researched, and watched when it can be.

    ``status`` is a plain string, like a watched company's board, so a new
    status needs no migration.
    """

    __tablename__ = "target_companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(100), unique=True)
    group_label: Mapped[str | None] = mapped_column(String(60))
    context: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    board: Mapped[str | None] = mapped_column(String(20))
    slug: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(300))
    checked_at: Mapped[datetime | None]
    added_at: Mapped[datetime] = mapped_column(default=_utcnow)


class InvestigationRow(Base):
    """A web investigation of a company and the report it produced (D67).

    Queued when asked for, run one at a time in the background. ``sources``
    is JSON: the numbered pages the report cites.
    """

    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(100))
    company_key: Mapped[str] = mapped_column(String(100), index=True)
    context: Mapped[str | None] = mapped_column(String(100))
    question: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    report: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    finished_at: Mapped[datetime | None]


class CompanyLanguageRow(Base):
    """A language a company asked for that the user does not speak (D69).

    The user notes it from an offer, having read the posting on LinkedIn;
    the company's other offers are then flagged. One row per company and
    language, matched on the exact company key so "Siemens Energy" does
    not carry Siemens' notes.
    """

    __tablename__ = "company_languages"
    __table_args__ = (UniqueConstraint("company_key", "language", name="uq_company_language"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200), index=True)
    language: Mapped[str] = mapped_column(String(40))
    noted_at: Mapped[datetime] = mapped_column(default=_utcnow)

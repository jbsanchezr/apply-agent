import itertools
from datetime import UTC, datetime, timedelta

import pytest

from apply_agent.domain import (
    ApplicationStage,
    ApplicationStatus,
    Event,
    MessageCategory,
    derive_status,
    stage_of,
)

C = MessageCategory
S = ApplicationStatus
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _events(*categories: MessageCategory) -> list[Event]:
    """Events one day apart, in the order given."""
    return [
        Event(
            message_id=f"m{i}",
            thread_id="t1",
            category=cat,
            occurred_at=T0 + timedelta(days=i),
            summary=cat.value,
        )
        for i, cat in enumerate(categories)
    ]


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ((), S.APPLIED),
        ((C.OTHER,), S.APPLIED),
        ((C.OTHER, C.INFORMATION_REQUEST), S.INFORMATION_REQUESTED),
        ((C.INFORMATION_REQUEST, C.INTERVIEW_INVITATION), S.INTERVIEWING),
        ((C.OTHER, C.INTERVIEW_INVITATION, C.REJECTION), S.REJECTED),
        ((C.INTERVIEW_INVITATION, C.OFFER), S.OFFER_RECEIVED),
        # Reference checks after an offer must not demote it.
        ((C.OFFER, C.OTHER), S.OFFER_RECEIVED),
        # "Still under review" after an interview must not reset progress.
        ((C.INTERVIEW_INVITATION, C.OTHER), S.INTERVIEWING),
        # A later invitation (e.g. the role reopened) supersedes a rejection.
        ((C.REJECTION, C.INTERVIEW_INVITATION), S.INTERVIEWING),
    ],
)
def test_status_follows_latest_status_bearing_event(
    history: tuple[MessageCategory, ...], expected: ApplicationStatus
) -> None:
    assert derive_status(_events(*history)) == expected


def test_status_is_independent_of_processing_order() -> None:
    events = _events(C.OTHER, C.INFORMATION_REQUEST, C.INTERVIEW_INVITATION, C.OFFER)
    results = {derive_status(perm) for perm in itertools.permutations(events)}
    assert results == {S.OFFER_RECEIVED}


def test_simultaneous_events_resolve_deterministically() -> None:
    a, b = (
        Event(
            message_id=mid,
            thread_id="t1",
            category=cat,
            occurred_at=T0,
            summary="same instant",
        )
        for mid, cat in (("m-a", C.INTERVIEW_INVITATION), ("m-b", C.REJECTION))
    )
    assert derive_status([a, b]) == derive_status([b, a]) == S.REJECTED


@pytest.mark.parametrize(
    ("status", "stage"),
    [
        (S.APPLIED, ApplicationStage.SENT),
        (S.INFORMATION_REQUESTED, ApplicationStage.ADVANCING),
        (S.INTERVIEWING, ApplicationStage.ADVANCING),
        (S.OFFER_RECEIVED, ApplicationStage.ADVANCING),
        (S.REJECTED, ApplicationStage.REJECTED),
    ],
)
def test_every_status_falls_in_one_stage(status: S, stage: ApplicationStage) -> None:
    assert stage_of(status) is stage


def test_acknowledgements_alone_leave_an_application_sent() -> None:
    assert stage_of(derive_status(_events(C.OTHER, C.OTHER))) is ApplicationStage.SENT

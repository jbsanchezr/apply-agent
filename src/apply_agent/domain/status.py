"""Deriving an application's status from its event history.

Status is a pure function of the events, not a field that each message
mutates. That makes it independent of processing order: re-running a sync, or
receiving messages out of order, always yields the same status.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from apply_agent.domain.enums import ApplicationStage, ApplicationStatus, MessageCategory
from apply_agent.domain.models import Event, StatusOverride

_STATUS_BY_CATEGORY: Final[Mapping[MessageCategory, ApplicationStatus]] = {
    MessageCategory.REJECTION: ApplicationStatus.REJECTED,
    MessageCategory.INTERVIEW_INVITATION: ApplicationStatus.INTERVIEWING,
    MessageCategory.INFORMATION_REQUEST: ApplicationStatus.INFORMATION_REQUESTED,
    MessageCategory.OFFER: ApplicationStatus.OFFER_RECEIVED,
}

# A company may reconsider and invite or hire after rejecting; little else
# arriving after a rejection means the application is open again.
_REOPENS_REJECTION: Final = frozenset({MessageCategory.INTERVIEW_INVITATION, MessageCategory.OFFER})


def derive_status(
    events: Iterable[Event], override: StatusOverride | None = None
) -> ApplicationStatus:
    """Return the status implied by the most recent status-bearing event.

    A rejection is only undone by an interview invitation or an offer: an
    information request after it is almost always a feedback survey or
    housekeeping that the classifier mistook for a request.

    With an ``override``, the status starts from what the user set, and only
    events dated after it count.

    ``OTHER`` events (acknowledgements, "still reviewing") never change the
    status. Events with identical timestamps are ordered by message id so the
    result is deterministic.
    """
    bearing = sorted(
        (
            e
            for e in events
            if e.category in _STATUS_BY_CATEGORY
            and (override is None or e.occurred_at > override.set_at)
        ),
        key=lambda e: (e.occurred_at, e.message_id),
    )
    status = ApplicationStatus.APPLIED if override is None else override.status
    for event in bearing:
        if status is ApplicationStatus.REJECTED and event.category not in _REOPENS_REJECTION:
            continue
        status = _STATUS_BY_CATEGORY[event.category]
    return status


_STAGE_BY_STATUS: Final[Mapping[ApplicationStatus, ApplicationStage]] = {
    ApplicationStatus.APPLIED: ApplicationStage.SENT,
    ApplicationStatus.INFORMATION_REQUESTED: ApplicationStage.ADVANCING,
    ApplicationStatus.INTERVIEWING: ApplicationStage.ADVANCING,
    ApplicationStatus.OFFER_RECEIVED: ApplicationStage.ADVANCING,
    ApplicationStatus.REJECTED: ApplicationStage.REJECTED,
}


def stage_of(status: ApplicationStatus) -> ApplicationStage:
    """Collapse a status into sent / advancing / rejected.

    ``SENT`` means only acknowledgements so far: nobody has replied yet.
    """
    return _STAGE_BY_STATUS[status]

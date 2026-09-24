"""Deriving an application's status from its event history.

Status is a pure function of the events, not a field that each message
mutates. That makes it independent of processing order: re-running a sync, or
receiving messages out of order, always yields the same status.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from apply_agent.domain.enums import ApplicationStatus, MessageCategory
from apply_agent.domain.models import Event

_STATUS_BY_CATEGORY: Final[Mapping[MessageCategory, ApplicationStatus]] = {
    MessageCategory.REJECTION: ApplicationStatus.REJECTED,
    MessageCategory.INTERVIEW_INVITATION: ApplicationStatus.INTERVIEWING,
    MessageCategory.INFORMATION_REQUEST: ApplicationStatus.INFORMATION_REQUESTED,
    MessageCategory.OFFER: ApplicationStatus.OFFER_RECEIVED,
}


def derive_status(events: Iterable[Event]) -> ApplicationStatus:
    """Return the status implied by the most recent status-bearing event.

    ``OTHER`` events (acknowledgements, "still reviewing") never change the
    status. Events with identical timestamps are ordered by message id so the
    result is deterministic.
    """
    bearing = [e for e in events if e.category in _STATUS_BY_CATEGORY]
    if not bearing:
        return ApplicationStatus.APPLIED
    latest = max(bearing, key=lambda e: (e.occurred_at, e.message_id))
    return _STATUS_BY_CATEGORY[latest.category]

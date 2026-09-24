"""Closed vocabularies shared by the classifier, the database and the API."""

from enum import StrEnum


class MessageCategory(StrEnum):
    """What a single inbound message means for a job application.

    These are the only labels the classifier may emit. ``OTHER`` covers both
    job-related messages that do not change anything (e.g. "we received your
    application") and unrelated noise.
    """

    REJECTION = "rejection"
    INTERVIEW_INVITATION = "interview_invitation"
    INFORMATION_REQUEST = "information_request"
    OTHER = "other"


class ApplicationStatus(StrEnum):
    """Where an application currently stands. Derived from its events."""

    APPLIED = "applied"
    INFORMATION_REQUESTED = "information_requested"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"

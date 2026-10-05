"""Pure domain layer: no I/O, no framework imports beyond Pydantic."""

from apply_agent.domain.assessment import MessageAssessment
from apply_agent.domain.enums import ApplicationStage, ApplicationStatus, MessageCategory
from apply_agent.domain.keys import company_key, role_key, similar_company
from apply_agent.domain.models import Application, Event, Message, StatusOverride
from apply_agent.domain.status import derive_status, stage_of

__all__ = [
    "Application",
    "ApplicationStage",
    "ApplicationStatus",
    "Event",
    "Message",
    "MessageAssessment",
    "MessageCategory",
    "StatusOverride",
    "company_key",
    "derive_status",
    "role_key",
    "similar_company",
    "stage_of",
]

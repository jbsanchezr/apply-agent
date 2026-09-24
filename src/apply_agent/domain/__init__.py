"""Pure domain layer: no I/O, no framework imports beyond Pydantic."""

from apply_agent.domain.enums import ApplicationStatus, MessageCategory
from apply_agent.domain.keys import company_key, role_key
from apply_agent.domain.models import Application, Event, Message
from apply_agent.domain.status import derive_status

__all__ = [
    "Application",
    "ApplicationStatus",
    "Event",
    "Message",
    "MessageCategory",
    "company_key",
    "derive_status",
    "role_key",
]

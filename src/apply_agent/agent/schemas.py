"""The contract between the LLM and the rest of the system.

The model never returns free text that we parse. Its only way to produce a
result is to call ``upsert_application``, whose arguments must validate as a
``MessageAssessment``. Invalid arguments are sent back as a tool error.
"""

from typing import Any, Final

from apply_agent.domain import MessageAssessment

LIST_NEW_MESSAGES: Final = "list_new_messages"
GET_THREAD: Final = "get_thread"
UPSERT_APPLICATION: Final = "upsert_application"


def tool_definitions() -> list[dict[str, Any]]:
    """Anthropic-format definitions of the tools the model may call."""
    return [
        {
            "name": GET_THREAD,
            "description": (
                "Return the earlier messages in the current email's thread, including the "
                "user's own sent messages. Use it when the email alone does not identify the "
                "company or the role."
            ),
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": UPSERT_APPLICATION,
            "description": (
                "Record your assessment of the current email. Call it exactly once per email."
            ),
            "input_schema": MessageAssessment.model_json_schema(),
        },
    ]

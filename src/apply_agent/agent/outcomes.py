"""What happened to each message in a sync, and the run-level summary."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Self

from apply_agent.domain import MessageCategory


class Outcome(StrEnum):
    RECORDED = "recorded"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MessageOutcome:
    message_id: str
    thread_id: str
    outcome: Outcome
    category: MessageCategory | None
    application_id: int | None
    reason: str | None
    steps: int  # model calls made for this message

    def log_fields(self) -> dict[str, Any]:
        # thread_id and message_id come from the correlated logger.
        return {
            "outcome": self.outcome.value,
            "category": self.category.value if self.category else None,
            "application_id": self.application_id,
            "reason": self.reason,
            "steps": self.steps,
        }


@dataclass(frozen=True, slots=True)
class SyncReport:
    processed: int
    recorded: int
    failed: int
    by_category: dict[str, int]
    failures: list[dict[str, Any]]

    @classmethod
    def from_outcomes(cls, outcomes: Iterable[MessageOutcome]) -> Self:
        items = list(outcomes)
        counts = Counter(o.outcome for o in items)
        return cls(
            processed=len(items),
            recorded=counts[Outcome.RECORDED],
            failed=counts[Outcome.FAILED],
            by_category=dict(Counter(o.category.value for o in items if o.category)),
            failures=[asdict(o) for o in items if o.outcome is Outcome.FAILED],
        )

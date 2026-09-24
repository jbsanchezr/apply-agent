"""Structured JSON logging with the email thread id as correlation id.

Stdlib only. Log records carry ids and outcomes, never email bodies or
subjects, so logs can be shipped anywhere without leaking message content.
"""

import json
import logging
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any, Final

CORRELATION_FIELDS: Final = ("thread_id", "message_id")
_RESERVED: Final = frozenset(vars(logging.makeLogRecord({})))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Anything passed via `extra=` (correlation ids, counts, outcomes).
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelatedLogger(logging.LoggerAdapter[logging.Logger]):
    """Adds the correlation ids to every record, merged with per-call extras."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        base: Mapping[str, object] = self.extra or {}
        kwargs["extra"] = {**base, **kwargs.get("extra", {})}
        return msg, kwargs


def correlated(logger: logging.Logger, *, thread_id: str, message_id: str) -> CorrelatedLogger:
    return CorrelatedLogger(logger, {"thread_id": thread_id, "message_id": message_id})


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

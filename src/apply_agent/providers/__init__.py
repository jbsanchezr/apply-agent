"""Read-only email sources behind a single interface."""

from apply_agent.providers.base import EmailProvider, ThreadNotFoundError
from apply_agent.providers.factory import make_provider
from apply_agent.providers.fake import FakeEmailProvider

__all__ = ["EmailProvider", "FakeEmailProvider", "ThreadNotFoundError", "make_provider"]

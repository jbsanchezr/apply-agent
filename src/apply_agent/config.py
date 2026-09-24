"""Runtime settings, read from ``APPLY_AGENT_*`` environment variables.

Unknown ``APPLY_AGENT_*`` variables are rejected, so a typo fails at startup
instead of silently falling back to a default.
"""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

ENV_PREFIX: Final = "APPLY_AGENT_"
# Credentials live outside the repository by default, so they cannot be committed.
_CONFIG_HOME: Final = Path("~/.config/apply_agent")

UserPath = Annotated[Path, AfterValidator(lambda p: p.expanduser())]


class ProviderKind(StrEnum):
    FAKE = "fake"
    GMAIL = "gmail"


class LlmKind(StrEnum):
    BASELINE = "baseline"
    ANTHROPIC = "anthropic"


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Settings(BaseModel):
    # validate_default so "~" in the default paths is expanded too.
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    database_url: str = "sqlite:///apply_agent.db"

    email_provider: ProviderKind = ProviderKind.FAKE
    fixtures_dir: UserPath = Path("tests/fixtures/emails")
    gmail_token_path: UserPath = _CONFIG_HOME / "gmail_token.json"
    gmail_client_secrets_path: UserPath = _CONFIG_HOME / "client_secret.json"

    # The keyword baseline needs no credentials; "anthropic" reads ANTHROPIC_API_KEY.
    llm: LlmKind = LlmKind.BASELINE
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Effort = Effort.LOW
    anthropic_refusal_fallback: bool = True

    max_agent_steps: int = Field(default=4, ge=1, le=10)
    initial_lookback_days: int = Field(default=90, ge=1)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        source = os.environ if env is None else env
        values = {
            key.removeprefix(ENV_PREFIX).lower(): value
            for key, value in source.items()
            if key.startswith(ENV_PREFIX)
        }
        return cls.model_validate(values)

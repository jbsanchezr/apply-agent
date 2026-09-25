"""Runtime settings, read from ``APPLY_AGENT_*`` environment variables.

Unknown ``APPLY_AGENT_*`` variables are rejected, so a typo fails at startup
instead of silently falling back to a default.
"""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, field_validator

ENV_PREFIX: Final = "APPLY_AGENT_"
# Credentials live outside the repository by default, so they cannot be committed.
_CONFIG_HOME: Final = Path("~/.config/apply_agent")

UserPath = Annotated[Path, AfterValidator(lambda p: p.expanduser())]


class ProviderKind(StrEnum):
    FAKE = "fake"
    GMAIL = "gmail"


class LlmKind(StrEnum):
    BASELINE = "baseline"
    OLLAMA = "ollama"
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

    # "baseline" needs nothing; "ollama" needs a local Ollama server; "anthropic"
    # reads ANTHROPIC_API_KEY.
    llm: LlmKind = LlmKind.BASELINE
    ollama_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"
    # Ollama's default context window is too small for the system prompt plus
    # an email plus its thread; a truncated prompt fails silently.
    ollama_num_ctx: int = Field(default=8_192, ge=2_048)
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Effort = Effort.LOW
    anthropic_refusal_fallback: bool = True

    # Tracing is off by default. When on, it needs LANGFUSE_PUBLIC_KEY,
    # LANGFUSE_SECRET_KEY and optionally LANGFUSE_HOST (read by the SDK itself).
    langfuse_enabled: bool = False
    langfuse_redact_bodies: bool = True

    # Protects the API. If unset, the API generates a random token at startup
    # and prints it once, so it is never open by accident.
    api_token: SecretStr | None = None

    max_agent_steps: int = Field(default=4, ge=1, le=10)
    initial_lookback_days: int = Field(default=90, ge=1)

    @field_validator("api_token", mode="before")
    @classmethod
    def _blank_token_means_unset(cls, value: object) -> object:
        # docker-compose passes an unset variable through as an empty string.
        return None if isinstance(value, str) and not value.strip() else value

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        source = os.environ if env is None else env
        values = {
            key.removeprefix(ENV_PREFIX).lower(): value
            for key, value in source.items()
            if key.startswith(ENV_PREFIX)
        }
        return cls.model_validate(values)

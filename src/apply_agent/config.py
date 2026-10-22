"""Runtime settings, read from ``APPLY_AGENT_*`` environment variables.

Unknown ``APPLY_AGENT_*`` variables are rejected, so a typo fails at startup
instead of silently falling back to a default.
"""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

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


class LinkedInSearch(BaseModel):
    """One search on LinkedIn's public job listings (D57)."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    keywords: str = Field(min_length=1, max_length=100)
    location: str = Field(default="", max_length=100)


def _parse_searches(value: object) -> object:
    """'data engineer @ Madrid; ml engineer' -> two searches, the second anywhere."""
    if not isinstance(value, str):
        return value
    searches = []
    for item in value.split(";"):
        if item.strip():
            keywords, _, location = item.partition("@")
            searches.append({"keywords": keywords, "location": location})
    return searches


def _parse_keywords(value: object) -> object:
    """'ai, llm , python' -> ('ai', 'llm', 'python')."""
    if not isinstance(value, str):
        return value
    return tuple(word.strip() for word in value.split(",") if word.strip())


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
    # An open application with no news for this long is flagged as quiet.
    quiet_after_days: int = Field(default=14, ge=1)

    # Offers are read from LinkedIn job alert emails. Searching LinkedIn's
    # public listings is off by default: it is scraping, which LinkedIn's terms
    # forbid and which LinkedIn may block at any time (see D57).
    linkedin_search_enabled: bool = False
    # "data engineer @ Madrid; machine learning engineer @ Spain"
    linkedin_searches: Annotated[tuple[LinkedInSearch, ...], BeforeValidator(_parse_searches)] = ()
    linkedin_search_pages: int = Field(default=2, ge=1, le=5)
    linkedin_search_days: int = Field(default=7, ge=1, le=30)

    # Your profile, to rank offers by fit (D58). Years of professional
    # experience (0 = just graduated) and words you want in a title, such as
    # "ai, llm, python, backend".
    profile_years_experience: int = Field(default=0, ge=0, le=50)
    profile_keywords: Annotated[tuple[str, ...], BeforeValidator(_parse_keywords)] = ()
    # Offers no alert has mentioned for this long are folded away as old.
    offers_old_after_days: int = Field(default=14, ge=1)

    @field_validator("api_token", mode="before")
    @classmethod
    def _blank_token_means_unset(cls, value: object) -> object:
        # docker-compose passes an unset variable through as an empty string.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _search_needs_queries(self) -> Self:
        if self.linkedin_search_enabled and not self.linkedin_searches:
            raise ValueError("linkedin_search_enabled needs at least one linkedin_searches entry")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        source = os.environ if env is None else env
        values = {
            key.removeprefix(ENV_PREFIX).lower(): value
            for key, value in source.items()
            if key.startswith(ENV_PREFIX)
        }
        return cls.model_validate(values)

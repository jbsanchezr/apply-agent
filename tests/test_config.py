from pathlib import Path

import pytest
from pydantic import ValidationError

from apply_agent.config import ProviderKind, Settings
from apply_agent.providers import FakeEmailProvider, make_provider
from apply_agent.providers.gmail_auth import GmailAuthError
from tests.conftest import FIXTURES_DIR


def test_defaults_to_the_fake_provider() -> None:
    settings = Settings.from_env({})
    assert settings.email_provider is ProviderKind.FAKE


def test_reads_prefixed_variables_and_ignores_others() -> None:
    settings = Settings.from_env(
        {"APPLY_AGENT_EMAIL_PROVIDER": "gmail", "HOME": "/root", "PATH": "/bin"}
    )
    assert settings.email_provider is ProviderKind.GMAIL


def test_typo_in_a_prefixed_variable_fails_fast() -> None:
    with pytest.raises(ValidationError, match="email_provder"):
        Settings.from_env({"APPLY_AGENT_EMAIL_PROVDER": "gmail"})


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env({"APPLY_AGENT_EMAIL_PROVIDER": "outlook"})


def test_credential_paths_default_outside_the_repository() -> None:
    settings = Settings.from_env({})
    repo = Path(__file__).parents[1].resolve()
    for path in (settings.gmail_token_path, settings.gmail_client_secrets_path):
        assert repo not in path.resolve().parents


def test_factory_builds_the_fake_provider() -> None:
    settings = Settings.from_env({"APPLY_AGENT_FIXTURES_DIR": str(FIXTURES_DIR / "emails")})
    assert isinstance(make_provider(settings), FakeEmailProvider)


def test_factory_refuses_gmail_without_a_token(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "APPLY_AGENT_EMAIL_PROVIDER": "gmail",
            "APPLY_AGENT_GMAIL_TOKEN_PATH": str(tmp_path / "missing.json"),
        }
    )
    with pytest.raises(GmailAuthError):
        make_provider(settings)


def test_blank_api_token_means_unset() -> None:
    assert Settings.from_env({"APPLY_AGENT_API_TOKEN": "  "}).api_token is None
    token = Settings.from_env({"APPLY_AGENT_API_TOKEN": "abc"}).api_token
    assert token is not None
    assert token.get_secret_value() == "abc"
    assert "abc" not in repr(Settings.from_env({"APPLY_AGENT_API_TOKEN": "abc"}))

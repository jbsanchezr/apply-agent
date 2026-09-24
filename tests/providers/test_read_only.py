"""Guards for hard constraint #1: the agent never sends, deletes or modifies mail."""

import json
import re
from pathlib import Path

import pytest

from apply_agent.providers import EmailProvider
from apply_agent.providers.gmail_api import GMAIL_READONLY_SCOPE, GmailApi
from apply_agent.providers.gmail_auth import (
    SCOPES,
    GmailAuthError,
    ensure_readonly,
    load_credentials,
)

SRC = Path(__file__).parents[2] / "src" / "apply_agent"
PROVIDERS = SRC / "providers"
# Gmail API methods that write. None of them may be called by the providers.
WRITE_CALLS = re.compile(
    r"\.(send|insert|import_|modify|batchModify|batchDelete|delete|trash|untrash|drafts|"
    r"settings|watch|stop)\s*\("
)


def test_only_the_read_only_scope_is_requested() -> None:
    assert SCOPES == (GMAIL_READONLY_SCOPE,)
    assert GMAIL_READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


@pytest.mark.parametrize(
    "scopes",
    [
        None,
        [],
        ["https://www.googleapis.com/auth/gmail.modify"],
        [GMAIL_READONLY_SCOPE, "https://www.googleapis.com/auth/gmail.send"],
        ["https://mail.google.com/"],
    ],
)
def test_any_scope_set_other_than_read_only_is_refused(scopes: list[str] | None) -> None:
    with pytest.raises(GmailAuthError):
        ensure_readonly(scopes)


def test_read_only_scope_is_accepted() -> None:
    ensure_readonly([GMAIL_READONLY_SCOPE])


def test_stored_token_with_broader_scope_is_refused(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "client_id": "fake-client.apps.googleusercontent.test",
                "client_secret": "fake-secret",
                "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GmailAuthError, match="read-only"):
        load_credentials(token)


def test_missing_token_explains_how_to_create_one(tmp_path: Path) -> None:
    with pytest.raises(GmailAuthError, match="gmail_auth"):
        load_credentials(tmp_path / "token.json")


@pytest.mark.parametrize("protocol", [EmailProvider, GmailApi])
def test_interfaces_expose_no_write_methods(protocol: type) -> None:
    public = {name for name in vars(protocol) if not name.startswith("_")}
    assert all(name.startswith(("list_", "get_")) for name in public), public


def _python_lines(root: Path) -> list[tuple[Path, int, str]]:
    return [
        (path, lineno, line)
        for path in sorted(root.rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
    ]


def test_google_client_is_confined_to_one_module() -> None:
    importers = {
        path.relative_to(SRC).as_posix()
        for path, _, line in _python_lines(SRC)
        if re.match(r"\s*(from|import) googleapiclient", line)
    }
    assert importers == {"providers/gmail_api.py"}


def test_providers_never_call_a_gmail_write_method() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{lineno}"
        for path, lineno, line in _python_lines(PROVIDERS)
        if WRITE_CALLS.search(line)
    ]
    assert offenders == []

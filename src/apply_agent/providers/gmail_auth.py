"""OAuth for the Gmail provider, restricted to the read-only scope.

The server never opens a browser: it only loads (and refreshes) an existing
token. The one-time consent flow is run by hand:

    uv run python -m apply_agent.providers.gmail_auth
"""

import json
import sys
from collections.abc import Iterable
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from apply_agent.config import Settings
from apply_agent.providers.gmail_api import GMAIL_READONLY_SCOPE

SCOPES: tuple[str, ...] = (GMAIL_READONLY_SCOPE,)


class GmailAuthError(RuntimeError):
    pass


def ensure_readonly(scopes: Iterable[str] | None) -> None:
    """Refuse any credential that could do more than read mail."""
    granted = set(scopes or ())
    if granted != {GMAIL_READONLY_SCOPE}:
        raise GmailAuthError(
            f"token must carry exactly the read-only Gmail scope, got {sorted(granted)}"
        )


def load_credentials(token_path: Path) -> Credentials:
    """Load a stored token, refreshing it if needed. Never prompts."""
    if not token_path.is_file():
        raise GmailAuthError(
            f"no Gmail token at {token_path}; "
            "run `uv run python -m apply_agent.providers.gmail_auth` first"
        )
    # No `scopes` argument: the scopes stored in the token file are loaded, so
    # a token minted with broader scopes is detected instead of masked.
    creds: Credentials = Credentials.from_authorized_user_file(str(token_path))
    ensure_readonly(creds.scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds, token_path)
    if not creds.valid:
        raise GmailAuthError(f"Gmail token at {token_path} is invalid; re-run the consent flow")
    return creds


def authorise_interactively(client_secrets_path: Path, token_path: Path) -> Credentials:
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), list(SCOPES))
    creds: Credentials = flow.run_local_server(port=0)
    ensure_readonly(creds.granted_scopes or creds.scopes)
    _save(creds, token_path)
    return creds


def _save(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)


def main() -> None:
    settings = Settings.from_env()
    authorise_interactively(settings.gmail_client_secrets_path, settings.gmail_token_path)
    # Check what actually landed on disk, since that is what the server will load.
    stored = json.loads(settings.gmail_token_path.read_text(encoding="utf-8"))
    ensure_readonly(stored.get("scopes"))
    sys.stdout.write(f"Read-only Gmail token saved to {settings.gmail_token_path}\n")


if __name__ == "__main__":
    main()

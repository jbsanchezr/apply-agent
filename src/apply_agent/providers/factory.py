"""Choose the email provider from settings."""

from typing import assert_never

from apply_agent.config import ProviderKind, Settings
from apply_agent.providers.base import EmailProvider
from apply_agent.providers.fake import FakeEmailProvider


def make_provider(settings: Settings) -> EmailProvider:
    match settings.email_provider:
        case ProviderKind.FAKE:
            return FakeEmailProvider(settings.fixtures_dir)
        case ProviderKind.GMAIL:
            # Imported lazily: the default (fake) path never loads Google libraries.
            from apply_agent.providers.gmail import GmailProvider
            from apply_agent.providers.gmail_api import GoogleGmailApi
            from apply_agent.providers.gmail_auth import load_credentials

            credentials = load_credentials(settings.gmail_token_path)
            return GmailProvider(GoogleGmailApi.from_credentials(credentials))
        case _:
            assert_never(settings.email_provider)

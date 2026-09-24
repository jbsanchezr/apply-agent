from pathlib import Path

import pytest

from apply_agent.providers import EmailProvider, FakeEmailProvider
from apply_agent.providers.parsing import MalformedEmailError
from tests.conftest import FIXTURES_DIR
from tests.providers.contract import EmailProviderContract


class TestFakeProviderContract(EmailProviderContract):
    @pytest.fixture
    def provider(self) -> EmailProvider:
        return FakeEmailProvider(FIXTURES_DIR / "emails")


def test_message_ids_are_rfc_message_ids() -> None:
    provider = FakeEmailProvider(FIXTURES_DIR / "emails")
    assert "ats-0019283@halcyonhealth.example" in {m.id for m in provider.list_messages()}


def test_thread_is_rooted_at_first_reference() -> None:
    provider = FakeEmailProvider(FIXTURES_DIR / "emails")
    kestrel = provider.get_thread("sent-88d0e2@mail.example.com")
    assert [m.id for m in kestrel] == [
        "k-5521@kestrel-robotics.example",
        "k-5790@kestrel-robotics.example",
        "k-6102@kestrel-robotics.example",
    ]


def test_message_without_references_is_its_own_thread() -> None:
    provider = FakeEmailProvider(FIXTURES_DIR / "emails")
    [halcyon] = provider.get_thread("ats-0019283@halcyonhealth.example")
    assert halcyon.thread_id == halcyon.id


def test_missing_directory_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FakeEmailProvider(tmp_path / "nope")


def test_email_without_message_id_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.eml").write_bytes(
        b"From: a@example.com\nDate: Mon, 01 Jun 2026 10:00:00 +0000\n\nhi\n"
    )
    with pytest.raises(MalformedEmailError, match="Message-ID"):
        FakeEmailProvider(tmp_path)

"""Behaviour every ``EmailProvider`` must have, run against each implementation.

Subclass ``EmailProviderContract`` and override the ``provider`` fixture.
"""

from collections.abc import Collection
from datetime import UTC, datetime, timedelta

import pytest

from apply_agent.domain import Message
from apply_agent.providers import EmailProvider, ThreadNotFoundError
from tests.conftest import FIXTURES_DIR

FIXTURE_COUNT = len(list((FIXTURES_DIR / "emails").glob("*.eml")))


def _by_subject(messages: list[Message], fragment: str) -> Message:
    matches = [m for m in messages if fragment in m.subject]
    assert matches, f"no message with {fragment!r} in the subject"
    return matches[0]


class EmailProviderContract:
    @pytest.fixture
    def provider(self) -> EmailProvider:
        raise NotImplementedError

    def test_lists_every_inbound_message(self, provider: EmailProvider) -> None:
        messages = list(provider.list_messages())
        assert len(messages) == FIXTURE_COUNT
        assert len({m.id for m in messages}) == FIXTURE_COUNT
        assert not any(m.outbound for m in messages)

    def test_lists_oldest_first(self, provider: EmailProvider) -> None:
        stamps = [m.sent_at for m in provider.list_messages()]
        assert stamps == sorted(stamps)

    def test_timestamps_are_utc(self, provider: EmailProvider) -> None:
        assert all(m.sent_at.tzinfo == UTC for m in provider.list_messages())

    def test_since_keeps_every_message_at_or_after_it(self, provider: EmailProvider) -> None:
        everything = list(provider.list_messages())
        cutoff = everything[len(everything) // 2].sent_at
        expected = {m.id for m in everything if m.sent_at >= cutoff}
        assert expected <= {m.id for m in provider.list_messages(since=cutoff)}

    def test_messages_the_caller_already_has_are_left_out(self, provider: EmailProvider) -> None:
        everything = [m.id for m in provider.list_messages()]
        seen = set(everything[::2])
        asked: list[str] = []

        def already_have(ids: Collection[str]) -> Collection[str]:
            asked.extend(ids)
            return seen & set(ids)

        listed = provider.list_messages(already_have=already_have)

        assert [m.id for m in listed] == [i for i in everything if i not in seen]
        assert set(asked) >= set(everything)

    def test_since_far_in_the_future_returns_nothing(self, provider: EmailProvider) -> None:
        future = datetime.now(UTC) + timedelta(days=365)
        assert list(provider.list_messages(since=future)) == []

    def test_every_listed_message_is_in_its_thread(self, provider: EmailProvider) -> None:
        for message in provider.list_messages():
            thread = list(provider.get_thread(message.thread_id))
            assert message in thread
            assert {m.thread_id for m in thread} == {message.thread_id}
            assert [m.sent_at for m in thread] == sorted(m.sent_at for m in thread)

    def test_replies_share_a_thread(self, provider: EmailProvider) -> None:
        messages = list(provider.list_messages())
        brightloom = _by_subject(messages, "Thank you for applying to Brightloom")
        inbound = [m for m in provider.get_thread(brightloom.thread_id) if not m.outbound]
        assert len(inbound) == 3

    def test_unrelated_messages_do_not_share_a_thread(self, provider: EmailProvider) -> None:
        messages = list(provider.list_messages())
        halcyon = _by_subject(messages, "MLOps Engineer at Halcyon Health")
        tidewater = _by_subject(messages, "Data Engineer (R-10442)")
        assert halcyon.thread_id != tidewater.thread_id

    def test_unknown_thread_raises(self, provider: EmailProvider) -> None:
        with pytest.raises(ThreadNotFoundError):
            provider.get_thread("no-such-thread")

    def test_html_only_body_becomes_plain_text(self, provider: EmailProvider) -> None:
        tidewater = _by_subject(list(provider.list_messages()), "Data Engineer (R-10442)")
        assert "not been selected" in tidewater.body_text
        assert "<" not in tidewater.body_text

    def test_non_ascii_body_is_decoded(self, provider: EmailProvider) -> None:
        quillon = _by_subject(list(provider.list_messages()), "Quillon Data")
        assert "Lamentablemente" in quillon.body_text
        assert "interés" in quillon.body_text

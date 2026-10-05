from datetime import UTC, datetime

import pytest

from apply_agent.providers import EmailProvider
from apply_agent.providers.gmail import BASE_QUERY, SINCE_MARGIN, GmailProvider
from tests.conftest import FIXTURES_DIR
from tests.providers.contract import FIXTURE_COUNT, EmailProviderContract
from tests.providers.fixture_gmail_api import FixtureGmailApi


@pytest.fixture
def api() -> FixtureGmailApi:
    return FixtureGmailApi(FIXTURES_DIR / "emails")


class TestGmailProviderContract(EmailProviderContract):
    @pytest.fixture
    def provider(self, api: FixtureGmailApi) -> EmailProvider:
        return GmailProvider(api)


def _brightloom_thread_id(provider: GmailProvider) -> str:
    return next(m.thread_id for m in provider.list_messages() if "Brightloom" in m.subject)


def test_own_sent_messages_appear_in_threads_as_outbound(api: FixtureGmailApi) -> None:
    provider = GmailProvider(api)
    thread = provider.get_thread(_brightloom_thread_id(provider))
    assert [m.outbound for m in thread] == [True, False, False, False]
    assert thread[0].subject == "Application - Machine Learning Engineer"


def test_drafts_are_never_returned(api: FixtureGmailApi) -> None:
    provider = GmailProvider(api)
    thread = provider.get_thread(_brightloom_thread_id(provider))
    assert all("follow-up" not in m.subject for m in thread)


def test_query_excludes_sent_spam_and_trash(api: FixtureGmailApi) -> None:
    GmailProvider(api).list_messages()
    assert api.queries[0] == BASE_QUERY
    for clause in ("-in:sent", "-in:drafts", "-in:spam", "-in:trash"):
        assert clause in BASE_QUERY


def test_since_is_widened_by_the_safety_margin(api: FixtureGmailApi) -> None:
    since = datetime(2026, 8, 1, tzinfo=UTC)
    GmailProvider(api).list_messages(since=since)
    assert api.queries[0].endswith(f"after:{int((since - SINCE_MARGIN).timestamp())}")


def test_all_pages_are_read(api: FixtureGmailApi) -> None:
    api.page_size = 4
    assert len(GmailProvider(api).list_messages()) == FIXTURE_COUNT
    assert len(api.queries) > 1


def test_listing_is_capped(api: FixtureGmailApi) -> None:
    assert len(GmailProvider(api, max_messages=5).list_messages()) == 5


def test_message_deleted_mid_sync_is_skipped(api: FixtureGmailApi) -> None:
    api.vanished.add("msg-000")
    assert len(GmailProvider(api).list_messages()) == FIXTURE_COUNT - 1


def test_messages_the_caller_already_has_are_not_downloaded(api: FixtureGmailApi) -> None:
    """A later sync re-lists days of processed mail; only the ids should cross the wire."""
    provider = GmailProvider(api)
    everything = [m.id for m in provider.list_messages()]
    seen, new = set(everything[:-2]), everything[-2:]
    api.fetched.clear()

    listed = provider.list_messages(already_have=lambda ids: [i for i in ids if i in seen])

    assert sorted(m.id for m in listed) == sorted(new)
    assert sorted(api.fetched) == sorted(new)


def test_nothing_is_downloaded_when_everything_is_known(api: FixtureGmailApi) -> None:
    assert GmailProvider(api).list_messages(already_have=lambda ids: ids) == []
    assert api.fetched == []

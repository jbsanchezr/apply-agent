"""The real ``googleapiclient`` adapter, driven offline through a mock HTTP transport.

These tests check what goes over the wire: every request is a GET against a
read endpoint, with ``format=raw`` for message bodies.
"""

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from apply_agent.providers import ThreadNotFoundError
from apply_agent.providers.gmail_api import (
    QUOTA_BACKOFF_SECONDS,
    GoogleGmailApi,
    MessageNotFoundError,
)

OK = {"status": "200"}
NOT_FOUND = {"status": "404"}
RAW = b"From: a@example.com\nSubject: hi\n\nbody\n"


def _api(*responses: tuple[dict[str, str], str]) -> tuple[GoogleGmailApi, HttpMockSequence]:
    http = HttpMockSequence(list(responses))
    service = build("gmail", "v1", http=http, static_discovery=True, cache_discovery=False)
    return GoogleGmailApi(service, num_retries=0), http


def _requests(http: HttpMockSequence) -> list[tuple[str, str]]:
    return [(method, uri) for uri, method, _body, _headers in http.request_sequence]


def test_list_sends_query_and_returns_next_page() -> None:
    body = {"messages": [{"id": "a", "threadId": "t"}], "nextPageToken": "p2"}
    api, http = _api((OK, json.dumps(body)))

    ids, token = api.list_message_ids("-in:sent", page_token=None)

    assert (ids, token) == (["a"], "p2")
    [(method, uri)] = _requests(http)
    assert method == "GET"
    assert urlparse(uri).path.endswith("/gmail/v1/users/me/messages")
    assert parse_qs(urlparse(uri).query)["q"] == ["-in:sent"]


def test_empty_listing() -> None:
    api, _ = _api((OK, "{}"))
    assert api.list_message_ids("", page_token=None) == ([], None)


def test_get_message_requests_raw_format_and_decodes_it() -> None:
    encoded = base64.urlsafe_b64encode(RAW).decode().rstrip("=")
    body = {
        "id": "a",
        "threadId": "t",
        "raw": encoded,
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1782900000000",
    }
    api, http = _api((OK, json.dumps(body)))

    message = api.get_raw_message("a")

    assert message.raw == RAW
    assert message.label_ids == {"INBOX", "UNREAD"}
    assert message.internal_date.timestamp() == 1_782_900_000
    [(method, uri)] = _requests(http)
    assert method == "GET"
    assert parse_qs(urlparse(uri).query)["format"] == ["raw"]


def test_get_thread_uses_minimal_format() -> None:
    api, http = _api((OK, json.dumps({"messages": [{"id": "a"}, {"id": "b"}]})))
    assert api.get_thread_message_ids("t") == ["a", "b"]
    [(method, uri)] = _requests(http)
    assert method == "GET"
    assert parse_qs(urlparse(uri).query)["format"] == ["minimal"]


def test_missing_thread_maps_to_domain_error() -> None:
    api, _ = _api((NOT_FOUND, "{}"))
    with pytest.raises(ThreadNotFoundError):
        api.get_thread_message_ids("t")


def test_missing_message_maps_to_domain_error() -> None:
    api, _ = _api((NOT_FOUND, "{}"))
    with pytest.raises(MessageNotFoundError):
        api.get_raw_message("a")


FORBIDDEN = {"status": "403"}
QUOTA_ERROR = json.dumps(
    {
        "error": {
            "code": 403,
            "message": "Quota exceeded for quota metric 'Total Query Cost' and limit "
            "'Units per minute per user' of service 'gmail.googleapis.com'",
            "errors": [{"reason": "rateLimitExceeded"}],
        }
    }
)


def _sleeping_api(
    *responses: tuple[dict[str, str], str],
) -> tuple[GoogleGmailApi, list[float]]:
    http = HttpMockSequence(list(responses))
    service = build("gmail", "v1", http=http, static_discovery=True, cache_discovery=False)
    slept: list[float] = []
    return GoogleGmailApi(service, num_retries=0, sleep=slept.append), slept


@pytest.mark.parametrize(
    "limited",
    [(FORBIDDEN, QUOTA_ERROR), ({"status": "429"}, "{}")],
    ids=["403-quota", "429"],
)
def test_per_user_quota_errors_are_waited_out(limited: tuple[dict[str, str], str]) -> None:
    """Regression: the first real sync of a 400-message inbox died on Gmail's quota."""
    api, slept = _sleeping_api(limited, limited, (OK, json.dumps({"messages": [{"id": "a"}]})))
    assert api.list_message_ids("", page_token=None) == (["a"], None)
    assert slept == list(QUOTA_BACKOFF_SECONDS[:2])


def test_quota_retries_eventually_give_up() -> None:
    attempts = [(FORBIDDEN, QUOTA_ERROR)] * (len(QUOTA_BACKOFF_SECONDS) + 1)
    api, slept = _sleeping_api(*attempts)
    with pytest.raises(HttpError):
        api.list_message_ids("", page_token=None)
    assert slept == list(QUOTA_BACKOFF_SECONDS)


def test_other_forbidden_errors_are_not_retried() -> None:
    denied = json.dumps({"error": {"code": 403, "message": "Insufficient Permission"}})
    api, slept = _sleeping_api((FORBIDDEN, denied))
    with pytest.raises(HttpError):
        api.list_message_ids("", page_token=None)
    assert slept == []

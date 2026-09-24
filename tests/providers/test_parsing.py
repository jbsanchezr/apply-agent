from datetime import UTC, datetime

import pytest

from apply_agent.providers.parsing import (
    MAX_BODY_CHARS,
    MalformedEmailError,
    header_ids,
    parse_message,
)

FALLBACK = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _parse(raw: str, **kwargs: object) -> object:
    return parse_message(raw.encode(), message_id="m1", thread_id="t1", **kwargs)  # type: ignore[arg-type]


def _email(date: str = "Wed, 01 Jul 2026 14:00:00 +0200", body: str = "Hello") -> bytes:
    return (
        "From: Recruiter <r@acme.example>\n"
        "Subject: =?utf-8?q?Entrevista_t=C3=A9cnica?=\n"
        f"Date: {date}\n"
        "Message-ID: <x@acme.example>\n"
        "\n"
        f"{body}\n"
    ).encode()


def test_basic_fields_and_encoded_subject() -> None:
    msg = parse_message(_email(), message_id="m1", thread_id="t1")
    assert msg.subject == "Entrevista técnica"
    assert msg.sender == "Recruiter <r@acme.example>"
    assert msg.sent_at == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert msg.body_text == "Hello"
    assert not msg.outbound


def test_unknown_origin_timezone_is_treated_as_utc() -> None:
    msg = parse_message(
        _email(date="Wed, 01 Jul 2026 12:00:00 -0000"), message_id="m", thread_id="t"
    )
    assert msg.sent_at == FALLBACK


def test_unparseable_date_uses_fallback() -> None:
    raw = _email(date="sometime last week")
    msg = parse_message(raw, message_id="m", thread_id="t", fallback_sent_at=FALLBACK)
    assert msg.sent_at == FALLBACK


def test_unparseable_date_without_fallback_is_an_error() -> None:
    with pytest.raises(MalformedEmailError, match="Date"):
        parse_message(_email(date="sometime last week"), message_id="m", thread_id="t")


def test_plain_text_part_is_preferred_over_html() -> None:
    raw = (
        b"From: a@acme.example\nDate: Wed, 01 Jul 2026 12:00:00 +0000\n"
        b'MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary="b"\n\n'
        b"--b\nContent-Type: text/plain\n\nplain version\n"
        b"--b\nContent-Type: text/html\n\n<p>html version</p>\n--b--\n"
    )
    assert parse_message(raw, message_id="m", thread_id="t").body_text == "plain version"


def test_unknown_charset_does_not_lose_the_message() -> None:
    raw = (
        b"From: a@acme.example\nDate: Wed, 01 Jul 2026 12:00:00 +0000\n"
        b"Content-Type: text/plain; charset=x-made-up\n\nstill readable\n"
    )
    assert "still readable" in parse_message(raw, message_id="m", thread_id="t").body_text


def test_huge_bodies_are_truncated() -> None:
    msg = parse_message(_email(body="x" * (MAX_BODY_CHARS * 2)), message_id="m", thread_id="t")
    assert len(msg.body_text) == MAX_BODY_CHARS


def test_missing_sender_gets_a_placeholder() -> None:
    raw = b"Date: Wed, 01 Jul 2026 12:00:00 +0000\n\nhi\n"
    assert parse_message(raw, message_id="m", thread_id="t").sender == "(unknown sender)"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("<a@x.example>", ["a@x.example"]),
        ("<a@x.example>\n <b@y.example>", ["a@x.example", "b@y.example"]),
        ("garbage", []),
        (None, []),
    ],
)
def test_header_ids(value: str | None, expected: list[str]) -> None:
    assert header_ids(value) == expected

"""Guards on the fixture corpus itself.

The corpus is the ground truth for tests and evaluation, and it is committed
to a public repo, so it gets the same scrutiny as code.
"""

import re
from collections import Counter
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from apply_agent.domain import MessageCategory
from tests.conftest import FIXTURES_DIR

EMAILS_DIR = FIXTURES_DIR / "emails"
EML_FILES = sorted(EMAILS_DIR.glob("*.eml"))

# RFC 2606 / RFC 6761 reserved names: guaranteed never to belong to anyone.
RESERVED_DOMAIN = re.compile(r"(^|\.)(example(\.(com|org|net))?|test|invalid)$")
EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
URL_HOST = re.compile(r"https?://([^/\s\"'<>:?]+)")
MIN_PER_CATEGORY = 3


class FixtureLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    category: MessageCategory
    company: str | None
    role: str | None
    notes: str


def _labels() -> list[FixtureLabel]:
    raw = (FIXTURES_DIR / "labels.json").read_bytes()
    return TypeAdapter(list[FixtureLabel]).validate_json(raw)


def _parse(path: Path) -> EmailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert isinstance(msg, EmailMessage)
    return msg


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    assert part is not None
    content = part.get_content()
    assert isinstance(content, str)
    return content


def test_corpus_has_expected_size() -> None:
    assert 20 <= len(EML_FILES) <= 30


def test_every_fixture_is_labelled_exactly_once() -> None:
    labelled = [label.file for label in _labels()]
    assert len(labelled) == len(set(labelled)), "duplicate label entries"
    assert set(labelled) == {p.name for p in EML_FILES}


def test_every_category_is_well_represented() -> None:
    counts = Counter(label.category for label in _labels())
    assert all(counts[c] >= MIN_PER_CATEGORY for c in MessageCategory), counts


def test_job_related_labels_name_a_company() -> None:
    for label in _labels():
        if label.category is not MessageCategory.OTHER:
            assert label.company, f"{label.file}: status-bearing message without company"


@pytest.mark.parametrize("path", EML_FILES, ids=lambda p: p.name)
def test_fixture_is_a_well_formed_email(path: Path) -> None:
    msg = _parse(path)
    for header in ("From", "To", "Subject", "Date", "Message-ID"):
        assert msg[header], f"missing {header}"
    assert parsedate_to_datetime(str(msg["Date"])).tzinfo is not None
    assert _body(msg).strip()


def test_message_ids_are_unique() -> None:
    ids = [str(_parse(p)["Message-ID"]) for p in EML_FILES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", EML_FILES, ids=lambda p: p.name)
def test_fixture_contains_only_reserved_domains(path: Path) -> None:
    msg = _parse(path)
    headers = [str(v) for h in ("From", "To", "Cc", "Reply-To") for v in msg.get_all(h, [])]
    domains = {addr.rsplit("@", 1)[-1] for _, addr in getaddresses(headers) if addr}
    body = _body(msg) + " ".join(str(v) for v in msg.values())
    domains |= set(EMAIL_IN_TEXT.findall(body)) | set(URL_HOST.findall(body))

    leaked = {d for d in domains if not RESERVED_DOMAIN.search(d.lower())}
    assert not leaked, f"non-reserved domains in {path.name}: {leaked}"

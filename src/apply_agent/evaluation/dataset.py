"""The labelled fixture corpus: ground truth for tests and evaluation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter

from apply_agent.domain import MessageCategory
from apply_agent.providers.parsing import header_ids, read_email

DEFAULT_FIXTURES_DIR: Final = Path("tests/fixtures")


class FixtureLabel(BaseModel):
    """One human label. ``notes`` explains why tricky cases are labelled as they are."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    category: MessageCategory
    company: str | None
    role: str | None
    is_job_application: bool
    notes: str


@dataclass(frozen=True, slots=True)
class LabelledMessage:
    message_id: str
    label: FixtureLabel


def load_labels(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> list[FixtureLabel]:
    raw = (fixtures_dir / "labels.json").read_bytes()
    return TypeAdapter(list[FixtureLabel]).validate_json(raw)


def load_dataset(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> list[LabelledMessage]:
    """Labels keyed by the message id the fake provider assigns (the RFC Message-ID)."""
    dataset = []
    for label in load_labels(fixtures_dir):
        headers = read_email((fixtures_dir / "emails" / label.file).read_bytes())
        [message_id] = header_ids(headers["Message-ID"])
        dataset.append(LabelledMessage(message_id, label))
    return dataset

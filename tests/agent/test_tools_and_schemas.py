from datetime import timedelta

import pytest
from pydantic import ValidationError

from apply_agent.agent.schemas import GET_THREAD, UPSERT_APPLICATION, tool_definitions
from apply_agent.agent.tools import SYNC_OVERLAP, Toolbox
from apply_agent.domain import MessageAssessment
from apply_agent.storage.repository import Repository
from tests.agent.helpers import T0, ListProvider, assessment, message


def test_the_model_is_offered_exactly_two_tools() -> None:
    assert [t["name"] for t in tool_definitions()] == [GET_THREAD, UPSERT_APPLICATION]


def test_upsert_schema_is_the_assessment_schema() -> None:
    upsert = tool_definitions()[1]
    assert upsert["input_schema"] == MessageAssessment.model_json_schema()
    assert upsert["input_schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (assessment("rejection", is_job=False, company=None), "requires is_job_application"),
        (assessment("rejection", company=None), "company is required"),
        (assessment("other", is_job=False, company="Acme"), "company must be null"),
        (assessment(summary=""), "summary"),
        (assessment(summary="x" * 201), "summary"),
        ({**assessment(), "confidence": 0.9}, "Extra inputs"),
        ({**assessment(), "category": "hired"}, "category"),
    ],
)
def test_assessment_rejects_inconsistent_output(args: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        MessageAssessment.model_validate(args)


def test_summary_is_forced_onto_one_line() -> None:
    parsed = MessageAssessment.model_validate(assessment(summary="Rejected.\n  Kind words."))
    assert parsed.summary == "Rejected. Kind words."


def _toolbox(provider: ListProvider, repository: Repository) -> Toolbox:
    return Toolbox(provider, repository, initial_lookback=timedelta(days=30), clock=lambda: T0)


def test_first_sync_looks_back_from_now(repository: Repository) -> None:
    provider = ListProvider([message("m1")])
    _toolbox(provider, repository).list_new_messages()
    assert provider.since_calls == [T0 - timedelta(days=30)]


def test_later_syncs_start_before_the_newest_recorded_message(repository: Repository) -> None:
    provider = ListProvider([message("m1", days=10), message("m2", days=11)])
    repository.record(provider.messages[0], MessageAssessment.model_validate(assessment()))

    new = _toolbox(provider, repository).list_new_messages()

    assert provider.since_calls == [provider.messages[0].sent_at - SYNC_OVERLAP]
    assert [m.id for m in new] == ["m2"], "already-processed messages are filtered out"

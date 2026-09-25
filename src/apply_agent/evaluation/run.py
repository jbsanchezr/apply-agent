"""Run the real agent over the labelled fixtures and score it.

The evaluation exercises the production path: the same graph, tools,
prompts and validation, against a throwaway SQLite database. It measures what
the product does, not what a bare classifier prompt would do.
"""

import tempfile
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from apply_agent.agent.graph import ChatModel
from apply_agent.agent.outcomes import SyncReport
from apply_agent.agent.runner import Agent
from apply_agent.agent.tools import Toolbox
from apply_agent.domain import Message, MessageAssessment, MessageCategory, company_key, role_key
from apply_agent.evaluation.dataset import DEFAULT_FIXTURES_DIR, load_dataset
from apply_agent.evaluation.metrics import ClassificationReport, classification_report, match_rate
from apply_agent.observability.observer import MetricsObserver
from apply_agent.observability.usage import UsageSummary, summarise
from apply_agent.providers import FakeEmailProvider
from apply_agent.storage import init_db, make_engine, make_session_factory
from apply_agent.storage.repository import RecordResult, Repository

_EVERYTHING = timedelta(days=36_500)  # fixtures have fixed dates; never filter them out


@dataclass(frozen=True, slots=True)
class MessageResult:
    file: str
    gold_category: str
    predicted_category: str | None
    gold_company: str | None
    predicted_company: str | None
    gold_role: str | None
    predicted_role: str | None
    notes: str


@dataclass(frozen=True, slots=True)
class EvalResult:
    model: str
    category: ClassificationReport
    is_job_application: ClassificationReport
    company_accuracy: float | None
    role_accuracy: float | None
    failures: list[dict[str, object]]
    messages: list[MessageResult]
    usage: UsageSummary
    latency_source: str  # "server-reported", "measured" or "unavailable (replayed)"


class _CapturingRepository(Repository):
    """Keeps every assessment in memory as well as recording it."""

    assessments: dict[str, MessageAssessment]

    def record(self, message: Message, assessment: MessageAssessment) -> RecordResult:
        self.assessments[message.id] = assessment
        return super().record(message, assessment)


def _run_agent(
    model: ChatModel, fixtures_dir: Path, max_steps: int, observer: MetricsObserver
) -> tuple[dict[str, MessageAssessment], SyncReport]:
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'eval.db'}")
        try:
            init_db(engine)
            repository = _CapturingRepository(make_session_factory(engine))
            repository.assessments = {}
            provider = FakeEmailProvider(fixtures_dir / "emails")
            toolbox = Toolbox(provider, repository, initial_lookback=_EVERYTHING)
            report = Agent(toolbox, model, max_steps, observer).sync()
            return repository.assessments, report
        finally:
            engine.dispose()  # release the file before the directory is removed


def evaluate(
    model: ChatModel,
    *,
    model_name: str,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    max_steps: int = 4,
    replayed: bool = False,
) -> EvalResult:
    dataset = load_dataset(fixtures_dir)
    observer = MetricsObserver()
    assessments, report = _run_agent(model, fixtures_dir, max_steps, observer)
    usage, latency_source = _usage(observer, replayed=replayed)
    predicted = [assessments.get(item.message_id) for item in dataset]
    labels = [item.label for item in dataset]

    category = classification_report(
        [label.category.value for label in labels],
        [p.category.value if p else None for p in predicted],
        [c.value for c in MessageCategory],
    )
    is_job = classification_report(
        [str(label.is_job_application).lower() for label in labels],
        [str(p.is_job_application).lower() if p else None for p in predicted],
        ["true", "false"],
    )
    companies = [
        (company_key(label.company), company_key(p.company) if p and p.company else None)
        for label, p in zip(labels, predicted, strict=True)
        if label.is_job_application and label.company
    ]
    roles = [
        (role_key(label.role), role_key(p.role) if p and p.role else None)
        for label, p in zip(labels, predicted, strict=True)
        if label.role
    ]
    return EvalResult(
        model=model_name,
        category=category,
        is_job_application=is_job,
        company_accuracy=match_rate(companies),
        role_accuracy=match_rate(roles),
        failures=report.failures,
        usage=usage,
        latency_source=latency_source,
        messages=[
            MessageResult(
                file=label.file,
                gold_category=label.category.value,
                predicted_category=p.category.value if p else None,
                gold_company=label.company,
                predicted_company=p.company if p else None,
                gold_role=label.role,
                predicted_role=p.role if p else None,
                notes=label.notes,
            )
            for label, p in zip(labels, predicted, strict=True)
        ],
    )


def _usage(observer: MetricsObserver, *, replayed: bool) -> tuple[UsageSummary, str]:
    """Server-reported latency survives replay; wall time only means something live."""
    records = observer.usage.records
    if any(r.model_seconds is not None for r in records):
        return summarise(records, use_model_latency=True), "server-reported"
    summary = summarise(records, use_model_latency=False)
    if replayed:
        no_latency = replace(summary, latency_p50_seconds=None, latency_p95_seconds=None)
        return no_latency, "unavailable (replayed)"
    return summary, "measured"

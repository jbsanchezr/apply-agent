"""Plain-text rendering of an evaluation (no table library needed)."""

from collections.abc import Sequence

from apply_agent.evaluation.metrics import ClassificationReport
from apply_agent.evaluation.run import EvalResult


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(str(cell)) for cell in column) for column in zip(header, *rows, strict=True)]
    fmt = "  ".join(f"{{:<{w}}}" if i == 0 else f"{{:>{w}}}" for i, w in enumerate(widths))
    lines = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    return lines + [fmt.format(*row) for row in rows]


def _per_class(report: ClassificationReport) -> list[str]:
    rows = [
        [c.label, _num(c.precision), _num(c.recall), _num(c.f1), str(c.support)]
        for c in report.per_class
    ]
    return _table(["class", "precision", "recall", "f1", "support"], rows)


def _confusion(report: ClassificationReport) -> list[str]:
    columns = list(next(iter(report.confusion.values())))
    short = [c.split("_")[0][:10] for c in columns]
    rows = [[gold, *(str(counts[c]) for c in columns)] for gold, counts in report.confusion.items()]
    return _table(["gold \\ predicted", *short], rows)


def format_report(result: EvalResult) -> str:
    cat = result.category
    low, high = cat.accuracy_ci
    lines = [
        f"Model: {result.model}",
        f"Messages: {cat.n}   Failed: {len(result.failures)}",
        "",
        f"Category accuracy: {cat.accuracy:.3f}  (95% CI {low:.2f}-{high:.2f})"
        f"   Macro-F1: {cat.macro_f1:.3f}",
        "",
        *_per_class(cat),
        "",
        "Confusion matrix (rows: gold label)",
        *_confusion(cat),
        "",
        f"is_job_application accuracy: {result.is_job_application.accuracy:.3f}",
        f"Company match: {_num(result.company_accuracy)}"
        f"   Role match: {_num(result.role_accuracy)}",
    ]
    wrong = [m for m in result.messages if m.predicted_category != m.gold_category]
    if wrong:
        lines += ["", "Misclassified:"]
        lines += [
            f"  {m.file:<28} gold={m.gold_category:<21} predicted={m.predicted_category}"
            for m in wrong
        ]
    return "\n".join(lines)

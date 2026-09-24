"""Classification metrics, stdlib only.

A missing prediction (the agent failed on a message) is scored as its own
``FAILED`` label: it counts against accuracy and recall, and it is visible in
the confusion matrix instead of silently disappearing from the denominator.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

FAILED: Final = "(failed)"


@dataclass(frozen=True, slots=True)
class ClassScores:
    label: str
    precision: float | None  # None when the class was never predicted
    recall: float | None  # None when the class never occurs in the gold labels
    f1: float | None
    support: int


@dataclass(frozen=True, slots=True)
class ClassificationReport:
    per_class: list[ClassScores]
    accuracy: float
    accuracy_ci: tuple[float, float]
    macro_f1: float
    confusion: dict[str, dict[str, int]]  # gold -> predicted -> count
    n: int


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float:
    p, r = precision or 0.0, recall or 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval: honest error bars for a small sample."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def classification_report(
    gold: Sequence[str], predicted: Sequence[str | None], labels: Sequence[str]
) -> ClassificationReport:
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted must have the same length")
    pred = [p if p is not None else FAILED for p in predicted]
    pairs = Counter(zip(gold, pred, strict=True))
    gold_counts, pred_counts = Counter(gold), Counter(pred)

    per_class = []
    for label in labels:
        tp = pairs[(label, label)]
        precision = _ratio(tp, pred_counts[label])
        recall = _ratio(tp, gold_counts[label])
        # A class that occurs but is never predicted scores F1 = 0, like scikit-learn;
        # only a class absent from both sides has no F1 at all.
        f1 = None if precision is None and recall is None else _f1(precision, recall)
        per_class.append(ClassScores(label, precision, recall, f1, gold_counts[label]))

    correct = sum(pairs[(label, label)] for label in labels)
    scored = [c.f1 for c in per_class if c.f1 is not None]
    columns = [*labels, *([FAILED] if pred_counts[FAILED] else [])]
    return ClassificationReport(
        per_class=per_class,
        accuracy=correct / len(gold) if gold else 0.0,
        accuracy_ci=wilson_interval(correct, len(gold)),
        macro_f1=sum(scored) / len(scored) if scored else 0.0,
        confusion={g: {p: pairs[(g, p)] for p in columns} for g in labels},
        n=len(gold),
    )


def match_rate(pairs: Sequence[tuple[str, str | None]]) -> float | None:
    """Share of (expected, predicted) pairs that match exactly."""
    return _ratio(sum(1 for expected, got in pairs if expected == got), len(pairs))

import pytest

from apply_agent.evaluation.metrics import (
    FAILED,
    classification_report,
    match_rate,
    wilson_interval,
)

LABELS = ["a", "b", "c"]


def test_per_class_precision_recall_and_accuracy() -> None:
    gold = ["a", "a", "b", "b", "c"]
    pred = ["a", "b", "b", "b", "a"]
    report = classification_report(gold, pred, LABELS)
    a, b, c = report.per_class

    assert (a.precision, a.recall, a.support) == (0.5, 0.5, 2)
    assert b.precision == pytest.approx(2 / 3)
    assert b.recall == 1.0
    assert (c.precision, c.recall, c.f1) == (None, 0.0, 0.0)
    assert report.accuracy == pytest.approx(3 / 5)
    assert report.confusion["c"] == {"a": 1, "b": 0, "c": 0}


def test_a_class_never_predicted_counts_as_zero_in_macro_f1() -> None:
    report = classification_report(["a", "b"], ["a", "a"], ["a", "b"])
    assert report.macro_f1 == pytest.approx((2 / 3 + 0.0) / 2)


def test_a_class_absent_on_both_sides_is_left_out_of_macro_f1() -> None:
    report = classification_report(["a"], ["a"], ["a", "b"])
    assert report.per_class[1].f1 is None
    assert report.macro_f1 == 1.0


def test_failed_predictions_count_against_accuracy_and_are_visible() -> None:
    report = classification_report(["a", "b"], ["a", None], ["a", "b"])
    assert report.accuracy == 0.5
    assert report.per_class[1].recall == 0.0
    assert report.confusion["b"][FAILED] == 1


def test_mismatched_lengths_are_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        classification_report(["a"], [], LABELS)


def test_wilson_interval_is_wide_for_small_samples() -> None:
    low, high = wilson_interval(19, 26)
    assert 0.5 < low < 19 / 26 < high < 0.9
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0)


def test_match_rate() -> None:
    assert match_rate([("x", "x"), ("y", None)]) == 0.5
    assert match_rate([]) is None

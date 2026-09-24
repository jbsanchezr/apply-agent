"""``scripts/evaluate.py``: score a model on the labelled fixtures.

Examples::

    uv run python scripts/evaluate.py                         # keyword baseline, free
    uv run python scripts/evaluate.py --llm anthropic \\
        --record eval_results/recordings/claude-opus-5.json   # live, records responses
    uv run python scripts/evaluate.py --llm anthropic \\
        --replay eval_results/recordings/claude-opus-5.json   # free, no API key needed
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from apply_agent.agent.llm import make_chat_model
from apply_agent.config import Effort, LlmKind, Settings
from apply_agent.evaluation.dataset import DEFAULT_FIXTURES_DIR
from apply_agent.evaluation.replay import RecordingCache, ReplayMissError
from apply_agent.evaluation.report import format_report
from apply_agent.evaluation.run import evaluate

EXIT_BELOW_THRESHOLD = 1
EXIT_STALE_RECORDING = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the agent on the labelled fixtures.")
    parser.add_argument("--llm", type=LlmKind, choices=list(LlmKind))
    parser.add_argument("--model", help="Anthropic model id (default from settings).")
    parser.add_argument("--effort", type=Effort, choices=list(Effort))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", type=Path, help="Call the model and store its responses here.")
    mode.add_argument("--replay", type=Path, help="Use stored responses only; never call the API.")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--output", type=Path, help="Also write the full result as JSON.")
    parser.add_argument("--min-accuracy", type=float, help="Exit 1 below this category accuracy.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    overrides = {
        key: value
        for key, value in {
            "llm": args.llm,
            "anthropic_model": args.model,
            "anthropic_effort": args.effort,
        }.items()
        if value is not None
    }
    settings = Settings.from_env().model_copy(update=overrides)
    recording = args.record or args.replay
    cache = RecordingCache(recording, replay_only=args.replay is not None) if recording else None

    name = "keyword baseline"
    if settings.llm is LlmKind.ANTHROPIC:
        name = f"{settings.anthropic_model} (effort {settings.anthropic_effort.value})"
    result = evaluate(
        make_chat_model(settings, cache=cache),
        model_name=name,
        fixtures_dir=args.fixtures,
        max_steps=settings.max_agent_steps,
    )

    if any(ReplayMissError.__name__ in str(f.get("reason")) for f in result.failures):
        sys.stderr.write(
            f"Recording {args.replay} is stale (prompt, tools or model changed). "
            "Re-record it with --record and a live API key.\n"
        )
        return EXIT_STALE_RECORDING

    sys.stdout.write(format_report(result) + "\n")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    if args.min_accuracy is not None and result.category.accuracy < args.min_accuracy:
        return EXIT_BELOW_THRESHOLD
    return 0

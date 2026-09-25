"""``scripts/evaluate.py``: score a model on the labelled fixtures.

Examples::

    uv run python scripts/evaluate.py                         # keyword baseline, free
    uv run python scripts/evaluate.py --llm ollama            # local model, free
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

from apply_agent.agent.llm import make_chat_model, model_fingerprint
from apply_agent.config import Effort, LlmKind, Settings
from apply_agent.evaluation.dataset import DEFAULT_FIXTURES_DIR
from apply_agent.evaluation.replay import RecordingCache, ReplayMissError
from apply_agent.evaluation.report import format_report
from apply_agent.evaluation.run import evaluate

EXIT_BELOW_THRESHOLD = 1
EXIT_STALE_RECORDING = 2
EXIT_MODEL_UNAVAILABLE = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the agent on the labelled fixtures.")
    parser.add_argument("--llm", type=LlmKind, choices=list(LlmKind))
    parser.add_argument("--model", help="Model id for the chosen --llm (default from settings).")
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
    settings = Settings.from_env()
    if args.llm is not None:
        settings = settings.model_copy(update={"llm": args.llm})
    model_field = "ollama_model" if settings.llm is LlmKind.OLLAMA else "anthropic_model"
    overrides = {model_field: args.model, "anthropic_effort": args.effort}
    settings = settings.model_copy(update={k: v for k, v in overrides.items() if v is not None})
    recording = args.record or args.replay
    cache = (
        RecordingCache(
            recording, replay_only=args.replay is not None, fingerprint=model_fingerprint(settings)
        )
        if recording
        else None
    )

    name = {
        LlmKind.BASELINE: "keyword baseline",
        LlmKind.OLLAMA: f"{settings.ollama_model} (Ollama, local)",
        LlmKind.ANTHROPIC: f"{settings.anthropic_model} (effort {settings.anthropic_effort.value})",
    }[settings.llm]
    result = evaluate(
        make_chat_model(settings, cache=cache),
        model_name=name,
        fixtures_dir=args.fixtures,
        max_steps=settings.max_agent_steps,
    )

    if any(ReplayMissError.__name__ in str(f.get("reason")) for f in result.failures):
        sys.stderr.write(
            f"Recording {args.replay} is stale (prompt, tools or model changed). "
            "Re-record it with --record and a live model.\n"
        )
        return EXIT_STALE_RECORDING

    if result.failures and len(result.failures) == len(result.messages):
        sys.stderr.write(
            f"Every message failed ({result.failures[0].get('reason')}). Is the model "
            f"reachable? For Ollama: is the server running and '{settings.ollama_model}' "
            "pulled?\n"
        )
        return EXIT_MODEL_UNAVAILABLE

    sys.stdout.write(format_report(result) + "\n")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(asdict(result), indent=2) + "\n"
        args.output.write_text(text, encoding="utf-8", newline="\n")
    if args.min_accuracy is not None and result.category.accuracy < args.min_accuracy:
        return EXIT_BELOW_THRESHOLD
    return 0

"""Command line entry point: ``python -m apply_agent sync``."""

import argparse
import json
import sys
from dataclasses import asdict

from apply_agent.agent.runner import build_agent
from apply_agent.config import Settings
from apply_agent.logs import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply_agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="Process new mail and update the applications table.")
    parser.parse_args(argv)

    configure_logging()
    agent, _ = build_agent(Settings.from_env())
    report = agent.sync()
    sys.stdout.write(json.dumps(asdict(report), indent=2) + "\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

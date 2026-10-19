"""Command line entry point: ``python -m apply_agent sync`` or ``offers``."""

import argparse
import json
import sys
from dataclasses import asdict

from apply_agent.agent.runner import build_agent
from apply_agent.config import Settings
from apply_agent.logs import configure_logging
from apply_agent.offers.scanner import build_offer_scanner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply_agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="Process new mail and update the applications table.")
    commands.add_parser("offers", help="Find job offers in LinkedIn alerts (and search, if on).")
    args = parser.parse_args(argv)

    configure_logging()
    settings = Settings.from_env()
    if args.command == "offers":
        scanner, _ = build_offer_scanner(settings)
        scan = scanner.scan()
        sys.stdout.write(json.dumps(asdict(scan), indent=2) + "\n")
        return 1 if scan.search_errors else 0
    agent, _ = build_agent(settings)
    report = agent.sync()
    sys.stdout.write(json.dumps(asdict(report), indent=2) + "\n")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

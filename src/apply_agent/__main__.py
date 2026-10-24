"""Command line entry point: ``python -m apply_agent sync`` or ``offers``.

``sync`` does what the page's *Sync now* does: mail, then alert offers, then
saved searches and watched companies.
"""

import argparse
import json
import sys
from dataclasses import asdict

from apply_agent.agent.runner import build_agent
from apply_agent.config import Settings
from apply_agent.logs import configure_logging
from apply_agent.offers.profile import load_profile
from apply_agent.offers.scanner import build_offer_scanner
from apply_agent.offers.search import build_job_searcher
from apply_agent.storage.offers import OfferRepository
from apply_agent.storage.searches import SearchRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply_agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="Process new mail, then look for job offers.")
    offers = commands.add_parser(
        "offers", help="Find job offers in LinkedIn alerts (and search, if on)."
    )
    offers.add_argument(
        "--rescan",
        action="store_true",
        help="Read every alert again and drop offers an older reader misread (D58).",
    )
    args = parser.parse_args(argv)

    configure_logging()
    settings = Settings.from_env()
    if args.command == "offers":
        scanner, _ = build_offer_scanner(settings)
        output: dict[str, object]
        if args.rescan:
            removed, scan = scanner.rescan()
            output = asdict(scan) | {"misread_removed": removed}
        else:
            scan = scanner.scan()
            output = asdict(scan)
        sys.stdout.write(json.dumps(output, indent=2) + "\n")
        return 1 if scan.search_errors else 0
    agent, repository = build_agent(settings)
    report = agent.sync()
    offer_store = OfferRepository(repository.session_factory)
    scanner, _ = build_offer_scanner(settings, offer_store)
    scan = scanner.scan()
    searches = SearchRepository(repository.session_factory)
    profile = load_profile(settings.profile_path)
    searched = build_job_searcher(settings, offer_store, searches, profile).run_saved()
    output = asdict(report) | {"offers": asdict(scan), "search": asdict(searched)}
    sys.stdout.write(json.dumps(output, indent=2) + "\n")
    return 1 if report.failed or scan.search_errors or searched.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

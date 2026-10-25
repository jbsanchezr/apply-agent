"""Find the job boards of the companies on the user's list (D66).

Looking for a board tries several slugs on each of the boards this app
reads: a few seconds per company, minutes for a long list. So it runs as a
background job, one company at a time, and records what it found as it goes.
A sure match is watched at once; a possible one waits for the user's word.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from apply_agent.domain.search import BoardStatus
from apply_agent.offers.boards import BoardError
from apply_agent.offers.search import JobSearcher
from apply_agent.storage.targets import TargetRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TargetCheckReport:
    checked: int = 0
    found: int = 0
    possible: int = 0
    none: int = 0
    errors: list[str] = field(default_factory=list)


def check_targets(
    targets: TargetRepository, searcher: JobSearcher, clock: Callable[[], datetime]
) -> TargetCheckReport:
    """Look for the board of every company still pending. One failure never stops the rest."""
    counts = dict.fromkeys(BoardStatus, 0)
    errors: list[str] = []
    pending = [t for t in targets.list() if t.status is BoardStatus.PENDING]
    for target in pending:
        try:
            match = searcher.find_company(target.name)
        except (httpx.HTTPError, BoardError, ValueError) as err:
            message = f"{type(err).__name__}: {err}"
            errors.append(f"{target.name}: {message}")
            targets.record(target.id, BoardStatus.ERROR, clock(), note=message)
            counts[BoardStatus.ERROR] += 1
            continue
        if match is None:
            targets.record(target.id, BoardStatus.NONE, clock())
            counts[BoardStatus.NONE] += 1
        elif match.confirmed:
            searcher.watch_board(target.name, match.board)
            targets.record(
                target.id,
                BoardStatus.FOUND,
                clock(),
                board=match.board.kind.value,
                slug=match.board.slug,
            )
            counts[BoardStatus.FOUND] += 1
        else:
            note = f"{match.postings} posting(s), e.g. " + "; ".join(match.examples)
            targets.record(
                target.id,
                BoardStatus.POSSIBLE,
                clock(),
                board=match.board.kind.value,
                slug=match.board.slug,
                note=note,
            )
            counts[BoardStatus.POSSIBLE] += 1
        logger.info("target company checked", extra={"status": counts})
    return TargetCheckReport(
        checked=len(pending),
        found=counts[BoardStatus.FOUND],
        possible=counts[BoardStatus.POSSIBLE],
        none=counts[BoardStatus.NONE],
        errors=errors,
    )

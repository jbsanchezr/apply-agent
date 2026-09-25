"""Background syncs: at most one at a time, observable by job id.

A sync with a local model takes minutes, far longer than an HTTP request
should stay open. ``POST /sync`` therefore starts a job on a single worker
thread and returns at once. The single worker is also the concurrency
control: a second sync is refused while one is queued or running, so two
syncs can never race on the same messages.
"""

import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from apply_agent.agent.outcomes import SyncReport

logger = logging.getLogger(__name__)
_KEEP_JOBS: Final = 20


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncJob:
    id: str
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None = None
    report: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return data


class SyncAlreadyRunningError(RuntimeError):
    def __init__(self, job: SyncJob) -> None:
        super().__init__(f"sync {job.id} is already running")
        self.job = job


class SyncService:
    def __init__(self, run_sync: Callable[[], SyncReport]) -> None:
        self._run_sync = run_sync
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, SyncJob] = OrderedDict()
        self._running: str | None = None

    def start(self) -> SyncJob:
        with self._lock:
            if self._running is not None:
                raise SyncAlreadyRunningError(self._jobs[self._running])
            job = SyncJob(id=uuid.uuid4().hex, status=JobStatus.RUNNING, started_at=_now())
            self._store(job)
            self._running = job.id
        self._executor.submit(self._execute, job.id)
        return job

    def get(self, job_id: str) -> SyncJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def latest(self) -> SyncJob | None:
        with self._lock:
            return next(reversed(self._jobs.values()), None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _execute(self, job_id: str) -> None:
        try:
            report = self._run_sync()
        except Exception as err:  # the job records the failure; the service keeps running
            logger.exception("background sync failed", extra={"job_id": job_id})
            self._finish(job_id, status=JobStatus.FAILED, error=f"{type(err).__name__}: {err}")
        else:
            self._finish(job_id, status=JobStatus.SUCCEEDED, report=asdict(report))

    def _finish(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._store(replace(self._jobs[job_id], finished_at=_now(), **changes))
            self._running = None

    def _store(self, job: SyncJob) -> None:
        self._jobs[job.id] = job
        while len(self._jobs) > _KEEP_JOBS:
            self._jobs.popitem(last=False)


def _now() -> datetime:
    return datetime.now(UTC)

"""The HTTP API, end to end through FastAPI's test client."""

import base64
import re
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from apply_agent.agent.outcomes import SyncReport
from apply_agent.api.app import create_app
from apply_agent.config import Settings
from apply_agent.domain import MessageAssessment
from apply_agent.storage import make_session_factory
from apply_agent.storage.repository import Repository
from tests.agent.helpers import T0, assessment, message
from tests.conftest import FIXTURES_DIR

TOKEN = "test-token-not-secret"  # noqa: S105 - fake token for tests
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EMPTY_REPORT = SyncReport(processed=0, recorded=0, failed=0, by_category={}, failures=[])


def _settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        {
            "APPLY_AGENT_DATABASE_URL": f"sqlite:///{tmp_path / 'api.db'}",
            "APPLY_AGENT_FIXTURES_DIR": str(FIXTURES_DIR / "emails"),
            "APPLY_AGENT_INITIAL_LOOKBACK_DAYS": "3650",
            "APPLY_AGENT_API_TOKEN": TOKEN,
            **extra,
        }
    )


def _wait_for(client: TestClient, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/sync/{job_id}", headers=AUTH).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"sync {job_id} did not finish within {timeout}s")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The real app: real agent (keyword baseline) over the fixtures, real SQLite."""
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


@pytest.fixture
def repository(engine: Engine) -> Repository:
    return Repository(make_session_factory(engine))


def test_health_and_metrics_need_no_token(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/metrics").text
    assert "# TYPE apply_agent_sync_runs_total counter" in body
    assert "apply_agent_applications" in body


def test_missing_token_gets_a_browser_login_prompt(client: TestClient) -> None:
    response = client.get("/applications")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {TOKEN}",
        "Basic " + base64.b64encode(f"anyone:{TOKEN}".encode()).decode(),
    ],
)
def test_bearer_and_basic_are_both_accepted(client: TestClient, header: str) -> None:
    assert client.get("/applications", headers={"Authorization": header}).status_code == 200


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong",
        "Basic !!!not-base64",
        "Basic " + base64.b64encode(b"user:").decode(),
        "Token x",
    ],
)
def test_bad_credentials_are_rejected(client: TestClient, header: str) -> None:
    assert client.get("/applications", headers={"Authorization": header}).status_code == 401


def test_sync_runs_in_the_background_and_fills_the_table(client: TestClient) -> None:
    started = client.post("/sync", headers=AUTH)
    assert started.status_code == 202

    job = _wait_for(client, started.json()["id"])
    assert job["status"] == "succeeded"
    assert job["report"]["processed"] == len(list((FIXTURES_DIR / "emails").glob("*.eml")))

    rows = client.get("/applications?format=json", headers=AUTH).json()
    assert rows
    assert {"company", "status", "latest_summary", "thread_ids"} <= set(rows[0])
    page = client.get("/applications", headers=AUTH).text
    assert "Kestrel Robotics" in page
    assert "Last sync: succeeded" in page

    again = _wait_for(client, client.post("/sync", headers=AUTH).json()["id"])
    assert again["report"]["processed"] == 0, "a second sync must be idempotent"


def test_json_is_also_chosen_by_accept_header(client: TestClient) -> None:
    response = client.get("/applications", headers={**AUTH, "Accept": "application/json"})
    assert response.headers["content-type"].startswith("application/json")


def test_a_second_sync_is_refused_while_one_is_running(
    tmp_path: Path, repository: Repository
) -> None:
    release = threading.Event()

    def slow_sync() -> SyncReport:
        release.wait(timeout=10)
        return EMPTY_REPORT

    app = create_app(_settings(tmp_path), run_sync=slow_sync, repository=repository)
    with TestClient(app) as client:
        first = client.post("/sync", headers=AUTH)
        second = client.post("/sync", headers=AUTH)
        release.set()

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["running"]["id"] == first.json()["id"]
        assert _wait_for(client, first.json()["id"])["status"] == "succeeded"
        assert client.post("/sync", headers=AUTH).status_code == 202, "the lock is released"


def test_a_failing_sync_is_reported_and_does_not_block_the_next(
    tmp_path: Path, repository: Repository
) -> None:
    def broken_sync() -> SyncReport:
        raise ConnectionError("mail server unreachable")

    app = create_app(_settings(tmp_path), run_sync=broken_sync, repository=repository)
    with TestClient(app) as client:
        job = _wait_for(client, client.post("/sync", headers=AUTH).json()["id"])
        assert job["status"] == "failed"
        assert "mail server unreachable" in job["error"]
        assert client.post("/sync", headers=AUTH).status_code == 202


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/sync/does-not-exist", headers=AUTH).status_code == 404


def test_email_derived_text_is_escaped_in_the_page(tmp_path: Path, repository: Repository) -> None:
    hostile = MessageAssessment.model_validate(
        assessment("offer", company="<script>alert(1)</script>", summary="<img src=x onerror=1>")
    )
    repository.record(message("m1"), hostile)
    app = create_app(_settings(tmp_path), run_sync=lambda: EMPTY_REPORT, repository=repository)
    with TestClient(app) as client:
        page = client.get("/applications", headers=AUTH).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img src=x" not in page


def test_without_a_configured_token_one_is_generated_and_printed(
    tmp_path: Path, repository: Repository, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path).model_copy(update={"api_token": None})
    app = create_app(settings, run_sync=lambda: EMPTY_REPORT, repository=repository)
    printed = re.search(r"^\s{4}(\S+)$", capsys.readouterr().err, re.MULTILINE)
    assert printed is not None
    with TestClient(app) as client:
        assert client.get("/applications").status_code == 401
        headers = {"Authorization": f"Bearer {printed.group(1)}"}
        assert client.get("/applications", headers=headers).status_code == 200


def _section(page: str, stage: str) -> str:
    match = re.search(rf'<section id="{stage}">(.*?)</section>', page, re.S)
    assert match, f"no {stage} section"
    return match.group(1)


def test_applications_are_grouped_by_stage_with_quiet_ones_flagged(
    tmp_path: Path, repository: Repository
) -> None:
    """Sent day 0, Nubaria interviewed day 2, Arcwell rejected day 5; viewed on day 30."""
    sent = assessment("other", company="Lumora Energy", role="Data Analyst")
    advancing = assessment("interview_invitation", company="Nubaria", role="ML Engineer")
    rejected = assessment("rejection", company="Arcwell Systems", role="Data Engineer")
    for i, (raw, days) in enumerate([(sent, 0), (advancing, 2), (rejected, 5)]):
        repository.record(
            message(f"m{i}", f"t{i}", days=days), MessageAssessment.model_validate(raw)
        )

    app = create_app(
        _settings(tmp_path, APPLY_AGENT_QUIET_AFTER_DAYS="28"),
        run_sync=lambda: EMPTY_REPORT,
        repository=repository,
        clock=lambda: T0 + timedelta(days=30),
    )
    with TestClient(app) as client:
        page = client.get("/applications", headers=AUTH).text
        rows = client.get("/applications?format=json", headers=AUTH).json()

    assert "Nubaria" in _section(page, "advancing")
    assert "Lumora Energy" in _section(page, "sent")
    assert "Arcwell Systems" in _section(page, "rejected")
    assert page.index('id="advancing"') < page.index('id="sent"') < page.index('id="rejected"')
    assert "<details>" in _section(page, "rejected"), "rejections are folded away"

    # 30 days without news on the sent one (quiet), 28 on the advancing one
    # (quiet, at the threshold), 25 on the rejection (never flagged).
    assert '<span class="quiet">30 days ago</span>' in _section(page, "sent")
    assert '<span class="quiet">28 days ago</span>' in _section(page, "advancing")
    assert '<span class="muted">25 days ago</span>' in _section(page, "rejected")
    assert "2 with no news lately" in page

    by_company = {r["company"]: r for r in rows}
    assert by_company["Lumora Energy"]["stage"] == "sent"
    assert by_company["Nubaria"]["stage"] == "advancing"
    assert by_company["Arcwell Systems"]["stage"] == "rejected"
    assert by_company["Lumora Energy"]["days_since_last_news"] == 30


def test_an_empty_table_asks_for_a_sync(tmp_path: Path, repository: Repository) -> None:
    app = create_app(_settings(tmp_path), run_sync=lambda: EMPTY_REPORT, repository=repository)
    with TestClient(app) as client:
        page = client.get("/applications", headers=AUTH).text
    assert "No applications yet. Run a sync." in page
    assert "<section" not in page

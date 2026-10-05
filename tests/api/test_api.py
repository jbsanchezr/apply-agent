"""The HTTP API, end to end through FastAPI's test client."""

import base64
import re
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Engine

from apply_agent.agent.outcomes import SyncReport
from apply_agent.api.app import create_app
from apply_agent.api.export import HEADERS, XLSX_MEDIA_TYPE
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


@pytest.fixture
def seeded(tmp_path: Path, repository: Repository) -> Iterator[TestClient]:
    """One application wrongly marked as advancing, seen 10 days later."""
    wrong = assessment("information_request", company="Halcyon Health", role="MLOps Engineer")
    repository.record(message("m1"), MessageAssessment.model_validate(wrong))
    app = create_app(
        _settings(tmp_path),
        run_sync=lambda: EMPTY_REPORT,
        repository=repository,
        clock=lambda: T0 + timedelta(days=10),
    )
    with TestClient(app) as client:
        yield client


def _first(client: TestClient) -> dict[str, Any]:
    row: dict[str, Any] = client.get("/applications?format=json", headers=AUTH).json()[0]
    return row


def test_a_false_positive_can_be_corrected_and_the_correction_undone(seeded: TestClient) -> None:
    app_id = _first(seeded)["id"]
    assert "Halcyon Health" in _section(seeded.get("/applications", headers=AUTH).text, "advancing")

    fixed = seeded.put(f"/applications/{app_id}/status", headers=AUTH, json={"status": "applied"})

    assert fixed.status_code == 200
    assert fixed.json()["status"] == "applied"
    assert fixed.json()["stage"] == "sent"
    assert _first(seeded)["status_overridden"] is True
    page = seeded.get("/applications", headers=AUTH).text
    assert "Halcyon Health" in _section(page, "sent")
    assert "set by hand" in _section(page, "sent")
    assert '<option value="auto">' in _section(page, "sent")

    undone = seeded.delete(f"/applications/{app_id}/status", headers=AUTH)

    assert undone.json()["status"] == "information_requested"
    assert _first(seeded)["status_overridden"] is False
    assert "set by hand" not in seeded.get("/applications", headers=AUTH).text


def test_the_correction_menu_offers_every_other_status(seeded: TestClient) -> None:
    page = seeded.get("/applications", headers=AUTH).text
    menu = re.search(r"<select.*?</select>", page, re.S)
    assert menu
    values = re.findall(r'<option value="([^"]*)"', menu.group(0))
    assert values == ["", "applied", "interviewing", "offer_received", "rejected"]


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("put", "/applications/999/status", {"status": "applied"}, 404),
        ("delete", "/applications/999/status", None, 404),
        ("put", "/applications/1/status", {"status": "hired"}, 422),
        ("put", "/applications/1/status", {"status": "applied", "extra": 1}, 422),
    ],
)
def test_bad_corrections_are_rejected(
    seeded: TestClient, method: str, path: str, body: dict[str, Any] | None, expected: int
) -> None:
    response = seeded.request(method, path, headers=AUTH, json=body)
    assert response.status_code == expected
    assert _first(seeded)["status"] == "information_requested"


@pytest.mark.parametrize("method", ["put", "delete"])
def test_corrections_need_the_token(seeded: TestClient, method: str) -> None:
    response = seeded.request(method, "/applications/1/status", json={"status": "applied"})
    assert response.status_code == 401


def _sheet(client: TestClient) -> Any:
    response = client.get("/applications?format=xlsx", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["content-disposition"] == (
        'attachment; filename="applications-2026-07-11.xlsx"'
    )
    return load_workbook(BytesIO(response.content))["Applications"]


def test_excel_export_has_one_row_per_application(seeded: TestClient) -> None:
    seeded.put("/applications/1/status", headers=AUTH, json={"status": "rejected"})

    rows = list(_sheet(seeded).iter_rows(values_only=True))

    assert rows[0] == HEADERS
    *texts, first_seen, last_news, days = rows[1]
    assert texts == [
        "Halcyon Health",
        "MLOps Engineer",
        "rejected",
        "rejected",
        "yes",
        "Something happened",
    ]
    assert first_seen.date() == last_news.date() == date(2026, 7, 1)
    assert days == 10
    assert len(rows) == 2


def test_excel_export_never_writes_email_text_as_a_formula(
    tmp_path: Path, repository: Repository
) -> None:
    """An email can put anything in a company name; Excel must not run it."""
    hostile = assessment(
        "offer", company='=HYPERLINK("http://evil.example","x")', role="+1+1", summary="@SUM(A1)"
    )
    repository.record(message("m1"), MessageAssessment.model_validate(hostile))
    app = create_app(
        _settings(tmp_path),
        run_sync=lambda: EMPTY_REPORT,
        repository=repository,
        clock=lambda: T0 + timedelta(days=10),
    )
    with TestClient(app) as client:
        sheet = _sheet(client)

    company, role, *_, latest = (sheet.cell(row=2, column=c) for c in (1, 2, 6))
    assert company.data_type == role.data_type == latest.data_type == "s"
    assert company.value == '=HYPERLINK("http://evil.example","x")'


def test_the_page_links_to_the_excel_export(seeded: TestClient) -> None:
    assert 'href="applications?format=xlsx"' in seeded.get("/applications", headers=AUTH).text


def test_excel_export_is_colour_coded_like_the_page(tmp_path: Path, repository: Repository) -> None:
    """Sent 30 days ago (quiet), interviewing 2 days ago, rejected 25 days ago."""
    rows = [
        (assessment("other", company="Lumora Energy", role="Data Analyst"), 0),
        (assessment("interview_invitation", company="Nubaria", role="ML Engineer"), 28),
        (assessment("rejection", company="Arcwell Systems", role="Data Engineer"), 5),
    ]
    for i, (raw, days) in enumerate(rows):
        repository.record(
            message(f"m{i}", f"t{i}", days=days), MessageAssessment.model_validate(raw)
        )
    app = create_app(
        _settings(tmp_path),
        run_sync=lambda: EMPTY_REPORT,
        repository=repository,
        clock=lambda: T0 + timedelta(days=30),
    )
    with TestClient(app) as client:
        response = client.get("/applications?format=xlsx", headers=AUTH)
    sheet = load_workbook(BytesIO(response.content))["Applications"]
    by_company = {row[0].value: row for row in sheet.iter_rows(min_row=2)}

    def colours(company: str) -> tuple[str, str, str | None]:
        _, _, stage, status, *_, days = by_company[company]
        font = days.font.color
        return stage.fill.start_color.rgb, status.fill.start_color.rgb, font and font.rgb

    assert colours("Lumora Energy") == ("FFF1F5F9", "FF64748B", "FFB45309")  # sent, quiet
    assert colours("Nubaria")[:2] == ("FFDCFCE7", "FF1D4ED8")  # advancing, interviewing
    assert colours("Nubaria")[2] != "FFB45309", "2 days without news is not quiet"
    assert colours("Arcwell Systems")[:2] == ("FFFEE2E2", "FFB91C1C")  # rejected
    assert colours("Arcwell Systems")[2] != "FFB45309", "a rejection is never flagged as quiet"
    assert by_company["Nubaria"][3].font.color.rgb == "FFFFFFFF"

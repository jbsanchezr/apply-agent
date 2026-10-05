"""The HTTP API: a thin layer over the agent and the repository.

    GET  /applications     HTML table, or JSON with ?format=json / Accept: application/json
    POST /sync             start a background sync (202), or 409 if one is running
    GET  /sync/{job_id}    job status and report
    GET  /metrics          Prometheus exposition (unauthenticated, no personal data)
    GET  /healthz          liveness (unauthenticated)

Run with ``uvicorn --factory apply_agent.api.app:create_app``.
"""

import secrets
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict

from apply_agent.agent.outcomes import SyncReport
from apply_agent.agent.runner import build_agent
from apply_agent.api.auth import TokenAuth
from apply_agent.api.export import XLSX_MEDIA_TYPE, applications_workbook
from apply_agent.api.jobs import SyncAlreadyRunningError, SyncService
from apply_agent.api.page import days_since, gmail_url, render_applications
from apply_agent.config import ProviderKind, Settings
from apply_agent.domain import Application, ApplicationStatus, stage_of
from apply_agent.logs import configure_logging
from apply_agent.observability.metrics import REGISTRY, ApplicationsCollector
from apply_agent.storage.repository import ApplicationNotFoundError, Repository


def _api_token(settings: Settings) -> str:
    if settings.api_token is not None:
        return settings.api_token.get_secret_value()
    token = secrets.token_urlsafe(24)
    # Printed once to stderr, not the structured log stream, like Jupyter's
    # startup token: the API is never open by accident, and never needs setup.
    sys.stderr.write(
        f"\nNo APPLY_AGENT_API_TOKEN set. Generated one for this run:\n\n    {token}\n\n"
        "Use it as the password in the browser prompt (any username), or as\n"
        "'Authorization: Bearer <token>'. Set APPLY_AGENT_API_TOKEN to keep it fixed.\n\n"
    )
    return token


class StatusCorrection(BaseModel):
    """Body of ``PUT /applications/{id}/status``."""

    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus


class MergeRequest(BaseModel):
    """Body of ``POST /applications/{id}/merge``."""

    model_config = ConfigDict(extra="forbid")

    into: int


def _application_json(application: Application) -> dict[str, Any]:
    return {
        **application.model_dump(mode="json"),
        "thread_ids": sorted(application.thread_ids),
        "stage": stage_of(application.status).value,
    }


def create_app(
    settings: Settings | None = None,
    *,
    run_sync: Callable[[], SyncReport] | None = None,
    repository: Repository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Build the app. ``run_sync``, ``repository`` and ``clock`` can be injected for tests."""
    settings = settings or Settings.from_env()
    if run_sync is None or repository is None:
        agent, built_repository = build_agent(settings)
        run_sync = run_sync or agent.sync
        repository = repository or built_repository
    repo = repository
    sync_service = SyncService(run_sync)
    applications = ApplicationsCollector(repo.count_by_status)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        REGISTRY.register(applications)
        try:
            yield
        finally:
            REGISTRY.unregister(applications)
            sync_service.shutdown()

    app = FastAPI(title="apply-agent", version="0.1.0", lifespan=lifespan)
    protected = [Depends(TokenAuth(_api_token(settings)))]

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/applications", dependencies=protected, response_model=None)
    def applications_view(request: Request, format: str | None = None) -> Response:
        views = repo.list_application_views()
        # Message ids are Gmail's only when the mail came from Gmail.
        gmail_links = settings.email_provider is ProviderKind.GMAIL
        now = clock()
        wants_json = format == "json" or (
            format is None and "application/json" in request.headers.get("accept", "")
        )
        if format == "xlsx":
            filename = f"applications-{now:%Y-%m-%d}.xlsx"
            return Response(
                applications_workbook(views, now, quiet_after_days=settings.quiet_after_days),
                media_type=XLSX_MEDIA_TYPE,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if wants_json:
            return JSONResponse(
                [
                    {
                        **_application_json(view.application),
                        "latest_summary": view.latest_summary,
                        "latest_category": view.latest_category,
                        "emails": [
                            {
                                "message_id": event.message_id,
                                "date": event.occurred_at.isoformat(),
                                "category": event.category.value,
                                "summary": event.summary,
                                "url": gmail_url(event.message_id) if gmail_links else None,
                            }
                            for event in view.events
                        ],
                        "days_since_last_news": days_since(view.application.last_activity_at, now),
                    }
                    for view in views
                ]
            )
        return HTMLResponse(
            render_applications(
                views,
                sync_service.latest(),
                now=now,
                quiet_after_days=settings.quiet_after_days,
                gmail_links=gmail_links,
            )
        )

    @app.put("/applications/{application_id}/status", dependencies=protected)
    def correct_status(application_id: int, correction: StatusCorrection) -> dict[str, Any]:
        """Set the status by hand. Emails dated after this moment still move it."""
        try:
            fixed = repo.set_status_override(application_id, correction.status, clock())
        except ApplicationNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown application") from err
        return _application_json(fixed)

    @app.delete("/applications/{application_id}/status", dependencies=protected)
    def clear_status_correction(application_id: int) -> dict[str, Any]:
        """Drop a manual correction and go back to the status the emails imply."""
        try:
            restored = repo.clear_status_override(application_id)
        except ApplicationNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown application") from err
        return _application_json(restored)

    @app.post("/applications/{application_id}/merge", dependencies=protected)
    def merge_application(application_id: int, request: MergeRequest) -> dict[str, Any]:
        """Fold a duplicate into the application it really is. This cannot be undone."""
        if application_id == request.into:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "an application cannot merge into itself"
            )
        try:
            merged = repo.merge_applications(application_id, into_id=request.into)
        except ApplicationNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown application") from err
        return _application_json(merged)

    @app.post("/sync", dependencies=protected, status_code=status.HTTP_202_ACCEPTED)
    def start_sync() -> Any:
        try:
            return sync_service.start().to_dict()
        except SyncAlreadyRunningError as err:
            return JSONResponse(
                {"detail": "a sync is already running", "running": err.job.to_dict()},
                status_code=status.HTTP_409_CONFLICT,
            )

    @app.get("/sync/{job_id}", dependencies=protected)
    def sync_status(job_id: str) -> dict[str, Any]:
        job = sync_service.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown sync job")
        return job.to_dict()

    return app

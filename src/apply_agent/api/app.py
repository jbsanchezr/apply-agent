"""The HTTP API: a thin layer over the agent and the repository.

    GET  /applications     HTML table, or JSON with ?format=json / Accept: application/json
    POST /sync             sync mail, then look for offers, in the background (202), or 409
    GET  /sync/{job_id}    job status and report
    GET  /offers           job offers found on LinkedIn, and whether you applied (D57)
    PUT  /offers/{id}/state  mark an offer as interesting, dismissed or new
    GET  /metrics          Prometheus exposition (unauthenticated, no personal data)
    GET  /healthz          liveness (unauthenticated)

Run with ``uvicorn --factory apply_agent.api.app:create_app``.
"""

import logging
import secrets
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import cache
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
from apply_agent.api.offers_page import OfferView, rank, render_offers, section_of
from apply_agent.api.page import days_since, gmail_url, render_applications
from apply_agent.config import ProviderKind, Settings
from apply_agent.domain import Application, ApplicationStatus, stage_of
from apply_agent.domain.fit import assess_fit
from apply_agent.domain.offers import OfferState, match_offer
from apply_agent.logs import configure_logging
from apply_agent.observability.metrics import REGISTRY, ApplicationsCollector
from apply_agent.offers.profile import load_profile
from apply_agent.offers.scanner import OfferScanReport, build_offer_scanner
from apply_agent.storage.offers import OfferNotFoundError, OfferRepository
from apply_agent.storage.repository import ApplicationNotFoundError, Repository

logger = logging.getLogger(__name__)


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


class OfferDecision(BaseModel):
    """Body of ``PUT /offers/{id}/state``."""

    model_config = ConfigDict(extra="forbid")

    state: OfferState


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
    run_offer_scan: Callable[[], OfferScanReport] | None = None,
    offers: OfferRepository | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Build the app. Its collaborators and ``clock`` can be injected for tests."""
    settings = settings or Settings.from_env()
    if run_sync is None or repository is None:
        agent, built_repository = build_agent(settings)
        run_sync = run_sync or agent.sync
        repository = repository or built_repository
    repo = repository
    offer_store = offers or OfferRepository(repo.session_factory)
    profile = load_profile(settings.profile_path)
    sync_mail, injected_scan = run_sync, run_offer_scan

    @cache
    def scan_offers_with_settings() -> Callable[[], OfferScanReport]:
        # Built on the first sync, not at startup: building it opens the mail
        # provider (the Gmail token), which serving the pages never needs.
        scanner, _ = build_offer_scanner(settings, offer_store)
        return scanner.scan

    def scan_offers() -> OfferScanReport:
        return (injected_scan or scan_offers_with_settings())()

    def sync_both_pages() -> dict[str, Any]:
        """Mail first, so an application sent today already counts against new offers."""
        report: dict[str, Any] = asdict(sync_mail())
        try:
            report["offers"] = asdict(scan_offers())
        except Exception as err:  # the mail is synced; a failed scan must not hide that
            logger.exception("offer scan failed")
            report["offers"] = {"error": f"{type(err).__name__}: {err}"}
        return report

    sync_service = SyncService(sync_both_pages)
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

    def offer_views(now: datetime) -> list[OfferView]:
        applications = repo.list_applications()
        old_before = now - timedelta(days=settings.offers_old_after_days)
        return [
            OfferView(
                stored,
                match_offer(stored.offer, applications),
                assess_fit(stored.offer, profile),
                old=stored.last_seen_at < old_before,
            )
            for stored in offer_store.list_offers()
        ]

    @app.get("/offers", dependencies=protected, response_model=None)
    def offers_view(request: Request, format: str | None = None) -> Response:
        now = clock()
        views = sorted(offer_views(now), key=rank)
        wants_json = format == "json" or (
            format is None and "application/json" in request.headers.get("accept", "")
        )
        if wants_json:
            return JSONResponse(
                [
                    {
                        "id": view.stored.id,
                        **view.stored.offer.model_dump(mode="json", exclude={"seen_at"}),
                        "state": view.stored.state.value,
                        "first_seen_at": view.stored.first_seen_at.isoformat(),
                        "last_seen_at": view.stored.last_seen_at.isoformat(),
                        "section": section_of(view),
                        "fit": view.fit.level.value,
                        "fit_score": view.fit.score,
                        "fit_reasons": list(view.fit.reasons),
                        "old": view.old,
                        "match": view.match.kind.value,
                        "application_id": view.match.application_id,
                        "application_status": (
                            view.match.application_status.value
                            if view.match.application_status
                            else None
                        ),
                    }
                    for view in views
                ]
            )
        return HTMLResponse(
            render_offers(
                views,
                sync_service.latest(),
                search_enabled=settings.linkedin_search_enabled,
                now=now,
                old_after_days=settings.offers_old_after_days,
            )
        )

    @app.put("/offers/{offer_id}/state", dependencies=protected)
    def decide_offer(offer_id: int, decision: OfferDecision) -> dict[str, Any]:
        try:
            stored = offer_store.set_state(offer_id, decision.state)
        except OfferNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown offer") from err
        return {"id": stored.id, "state": stored.state.value}

    return app

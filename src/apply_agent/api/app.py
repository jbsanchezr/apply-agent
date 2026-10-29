"""The HTTP API: a thin layer over the agent and the repository.

    GET  /applications     HTML table, or JSON with ?format=json / Accept: application/json
    POST /sync             sync mail, then look for offers, in the background (202), or 409
    GET  /sync/{job_id}    job status and report
    GET  /offers           job offers found on LinkedIn, and whether you applied (D57)
    PUT  /offers/{id}/state  mark an offer as interesting, dismissed or new
    PUT  /offers/{id}/closed  the posting is no longer open (DELETE to undo)
    GET  /metrics          Prometheus exposition (unauthenticated, no personal data)
    GET  /healthz          liveness (unauthenticated)

Run with ``uvicorn --factory apply_agent.api.app:create_app``.
"""

import logging
import secrets
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apply_agent.agent.llm import make_text_model
from apply_agent.agent.outcomes import SyncReport
from apply_agent.agent.runner import build_agent
from apply_agent.api.auth import TokenAuth
from apply_agent.api.companies_page import CompanyRow, render_companies
from apply_agent.api.company_page import render_company
from apply_agent.api.export import XLSX_MEDIA_TYPE, applications_workbook
from apply_agent.api.jobs import JobStatus, SyncAlreadyRunningError, SyncService
from apply_agent.api.offers_page import OfferView, OldAfter, rank, render_offers, section_of
from apply_agent.api.page import days_since, gmail_url, render_applications
from apply_agent.api.search_page import render_search
from apply_agent.config import LlmKind, ProviderKind, Settings
from apply_agent.domain import Application, ApplicationStatus, stage_of
from apply_agent.domain.fit import assess_fit
from apply_agent.domain.keys import company_key, similar_company
from apply_agent.domain.languages import language_named
from apply_agent.domain.offers import JobOffer, OfferState, match_offer, typical_closing_age
from apply_agent.domain.search import BoardStatus, JobSearch, TargetCompany
from apply_agent.logs import configure_logging
from apply_agent.observability.metrics import REGISTRY, ApplicationsCollector
from apply_agent.offers.boards import Board, BoardError, BoardKind, board_from_url
from apply_agent.offers.company import CompanyResearcher, NewsItem, company_offers, summarize
from apply_agent.offers.company import make_client as make_company_client
from apply_agent.offers.investigate import Investigator, run_queued
from apply_agent.offers.pasted import parse_pasted_search
from apply_agent.offers.profile import load_profile
from apply_agent.offers.scanner import OfferScanReport, build_offer_scanner
from apply_agent.offers.search import (
    JobSearcher,
    SearchUnavailableError,
    build_job_searcher,
)
from apply_agent.offers.targets import check_targets
from apply_agent.offers.web import PageReader, SearxngSearch
from apply_agent.offers.web import make_client as make_web_client
from apply_agent.storage.investigations import InvestigationRepository, InvestigationStatus
from apply_agent.storage.languages import CompanyLanguages
from apply_agent.storage.offers import OfferNotFoundError, OfferRepository
from apply_agent.storage.repository import ApplicationNotFoundError, Repository
from apply_agent.storage.searches import SearchRepository
from apply_agent.storage.targets import TargetRepository, parse_company_list

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


class LanguageRequest(BaseModel):
    """Body of ``POST /offers/{id}/language``: the language the posting asks for."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    language: str = Field(min_length=2, max_length=40)


class PasteRequest(BaseModel):
    """Body of ``POST /offers/paste``: a LinkedIn search page as copied from the browser."""

    model_config = ConfigDict(extra="forbid")

    # A whole results page, as HTML, is a few megabytes at most.
    html: str = Field(min_length=1, max_length=10_000_000)
    save: bool = False


class CareersLinkRequest(BaseModel):
    """Body of ``POST /companies/{id}/board``: the address of the company's careers page."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=8, max_length=500)


class InvestigateRequest(BaseModel):
    """Body of ``POST /company/investigate``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    context: str | None = Field(default=None, max_length=100)
    question: str | None = Field(default=None, max_length=300)


class ImportRequest(BaseModel):
    """Body of ``POST /companies/import``: a pasted list of companies."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)


class SummaryRequest(BaseModel):
    """Body of ``POST /company/summary``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    context: str | None = Field(default=None, max_length=100)


class WatchRequest(BaseModel):
    """Body of ``POST /search/companies``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=300)
    keywords: str = Field(default="", max_length=300)
    places: str = Field(default="", max_length=300)
    # Watch a board found by guessing even though it may be another company's.
    confirm: bool = False

    @property
    def keyword_list(self) -> list[str]:
        return [w.strip() for w in self.keywords.split(",") if w.strip()]

    @property
    def place_list(self) -> list[str]:
        return [w.strip() for w in self.places.split(",") if w.strip()]


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
    searcher: JobSearcher | None = None,
    researcher: CompanyResearcher | None = None,
    summarizer: Callable[[str, Sequence[NewsItem]], str] | None = None,
    investigator: Investigator | None = None,
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
    search_store = SearchRepository(repo.session_factory)
    sync_mail, injected_scan = run_sync, run_offer_scan
    job_searcher = searcher or build_job_searcher(settings, offer_store, search_store, profile)
    company_researcher = researcher or CompanyResearcher(make_company_client(), clock=clock)

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
        try:
            report["search"] = asdict(job_searcher.run_saved())
        except Exception as err:  # same: searches failing must not hide the rest
            logger.exception("saved searches failed")
            report["search"] = {"errors": [f"{type(err).__name__}: {err}"]}
        return report

    sync_service = SyncService(sync_both_pages)
    target_store = TargetRepository(repo.session_factory)
    # Looking for job boards takes seconds per company: a job of its own (D66).
    target_service = SyncService(lambda: asdict(check_targets(target_store, job_searcher, clock)))
    investigation_store = InvestigationRepository(repo.session_factory)
    company_languages = CompanyLanguages(repo.session_factory)

    @cache
    def web_investigator() -> Investigator | None:
        """Built on first use, and only with a SearXNG to search (D67)."""
        if investigator is not None:
            return investigator
        if settings.searxng_url is None:
            return None
        return Investigator(
            SearxngSearch(make_web_client(), settings.searxng_url),
            PageReader(make_web_client()),
            text_model,
        )

    def run_investigations() -> dict[str, int]:
        found = web_investigator()
        if found is None:
            raise RuntimeError("web research is off: set APPLY_AGENT_SEARXNG_URL")
        return run_queued(investigation_store, found, profile, clock)

    # Investigations take minutes with a local model: a queue run one at a time.
    investigation_service = SyncService(run_investigations)
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
            target_service.shutdown()
            investigation_service.shutdown()

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

    def old_after() -> OldAfter:
        """Learned from offers the user found closed, else the setting (D61)."""
        ages = offer_store.closing_ages()
        learned = typical_closing_age(ages)
        if learned is None:
            return OldAfter(settings.offers_old_after_days, closures=len(ages), learned=False)
        return OldAfter(learned, closures=len(ages), learned=True)

    def offer_views(now: datetime, fold: OldAfter) -> list[OfferView]:
        applications = repo.list_applications()
        old_before = now - timedelta(days=fold.days)
        asked = company_languages.by_company()
        return [
            OfferView(
                stored,
                match_offer(stored.offer, applications),
                assess_fit(
                    stored.offer,
                    profile,
                    asked_before=asked.get(company_key(stored.offer.company), ()),
                ),
                old=stored.last_seen_at < old_before,
            )
            for stored in offer_store.list_offers()
        ]

    @app.post("/offers/{offer_id}/language", dependencies=protected)
    def offer_needs_language(offer_id: int, request: LanguageRequest) -> dict[str, Any]:
        """The posting asks for a language the user lacks: note it and dismiss the offer.

        The company's other offers are flagged from now on (D69).
        """
        language = language_named(request.language)
        if language is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"not a language: {request.language}"
            )
        try:
            stored = offer_store.set_state(offer_id, OfferState.DISMISSED)
        except OfferNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown offer") from err
        company_languages.note(stored.offer.company, language, clock())
        return {"id": offer_id, "company": stored.offer.company, "language": language}

    @app.post("/offers/paste", dependencies=protected)
    def paste_search(request: PasteRequest) -> dict[str, Any]:
        """Offers on a LinkedIn search page the user pasted (D73).

        Without ``save`` it only says what it read, for the user to check; with
        it, the offers join the others.
        """
        page = parse_pasted_search(request.html, clock())
        known = offer_store.known_keys([o.key for o in page.offers])
        asked = company_languages.by_company()
        found = []
        for offer in page.offers:
            fit = assess_fit(offer, profile, asked_before=asked.get(company_key(offer.company), ()))
            found.append(
                {
                    "title": offer.title,
                    "company": offer.company,
                    "location": offer.location,
                    "url": offer.url,
                    "fit": fit.level.value,
                    "fit_score": fit.score,
                    "known": offer.key in known,
                }
            )
        new = offer_store.save(page.offers) if request.save else 0
        return {"offers": found, "unreadable": page.unreadable, "new": new}

    @app.get("/offers", dependencies=protected, response_model=None)
    def offers_view(request: Request, format: str | None = None) -> Response:
        now, fold = clock(), old_after()
        views = sorted(offer_views(now, fold), key=rank)
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
                        "closed_at": (
                            view.stored.closed_at.isoformat() if view.stored.closed_at else None
                        ),
                        "days_since_last_alert": days_since(view.stored.last_seen_at, now),
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
                old_after=fold,
            )
        )

    @app.put("/offers/{offer_id}/closed", dependencies=protected)
    def close_offer(offer_id: int) -> dict[str, Any]:
        """The posting is no longer open. Its age teaches when to fold offers (D61)."""
        try:
            stored = offer_store.mark_closed(offer_id, clock())
        except OfferNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown offer") from err
        return {
            "id": stored.id,
            "closed_at": stored.closed_at.isoformat() if stored.closed_at else None,
        }

    @app.delete("/offers/{offer_id}/closed", dependencies=protected)
    def reopen_offer(offer_id: int) -> dict[str, Any]:
        """Undo a closure marked by mistake."""
        try:
            stored = offer_store.reopen(offer_id)
        except OfferNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown offer") from err
        return {"id": stored.id, "closed_at": None}

    @app.put("/offers/{offer_id}/state", dependencies=protected)
    def decide_offer(offer_id: int, decision: OfferDecision) -> dict[str, Any]:
        try:
            stored = offer_store.set_state(offer_id, decision.state)
        except OfferNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown offer") from err
        return {"id": stored.id, "state": stored.state.value}

    def views_for(found: Sequence[JobOffer], now: datetime) -> list[OfferView]:
        """Stored offers for search results, with their fit and applied check."""
        keys = {offer.key for offer in found}
        fold = old_after()
        return [v for v in offer_views(now, fold) if v.stored.offer.key in keys]

    @cache
    def text_model() -> Runnable[LanguageModelInput, BaseMessage] | None:
        # Built on first use: loading the model client is not needed to serve pages.
        return make_text_model(settings)

    def can_summarize() -> bool:
        return summarizer is not None or settings.llm is not LlmKind.BASELINE

    def start_target_check() -> dict[str, Any]:
        try:
            return target_service.start().to_dict()
        except SyncAlreadyRunningError as err:
            return err.job.to_dict()

    @app.get("/companies", dependencies=protected, response_class=HTMLResponse)
    def companies_view() -> HTMLResponse:
        """The user's companies, each with its job board and offers (D66)."""
        now = clock()
        views = offer_views(now, old_after())
        stored = [v.stored for v in views]
        by_id = {v.stored.id: v for v in views}
        week = now - timedelta(days=7)
        rows = []
        for target in target_store.list():
            mine = tuple(by_id[o.id] for o in company_offers(stored, target.name))
            new = sum(1 for v in mine if v.stored.first_seen_at >= week)
            rows.append(CompanyRow(target, mine, new))
        return HTMLResponse(render_companies(rows, target_service.latest()))

    @app.post("/companies/import", dependencies=protected, status_code=status.HTTP_202_ACCEPTED)
    def import_companies(request: ImportRequest) -> dict[str, Any]:
        """Add a pasted list of companies and look for their job boards in the background."""
        added = target_store.add(parse_company_list(request.text))
        return {"added": added, "job": start_target_check()}

    @app.post("/companies/check", dependencies=protected, status_code=status.HTTP_202_ACCEPTED)
    def check_companies() -> dict[str, Any]:
        """Look again for the boards of companies not found yet or that failed."""
        for target in target_store.list():
            if target.status in (BoardStatus.ERROR, BoardStatus.NONE):
                target_store.record(target.id, BoardStatus.PENDING, clock())
        return start_target_check()

    @app.get("/companies/check/{job_id}", dependencies=protected)
    def company_check_status(job_id: str) -> dict[str, Any]:
        job = target_service.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown check")
        return job.to_dict()

    def target_or_404(target_id: int) -> TargetCompany:
        target = target_store.get(target_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown company")
        return target

    @app.post("/companies/{target_id}/confirm", dependencies=protected)
    def confirm_company_board(target_id: int) -> dict[str, Any]:
        """The possible board is the company's: watch it."""
        target = target_or_404(target_id)
        if target.status is not BoardStatus.POSSIBLE or not target.board or not target.slug:
            raise HTTPException(status.HTTP_409_CONFLICT, "nothing to confirm")
        board = Board(BoardKind(target.board), target.slug)
        _, kept, new = job_searcher.watch_board(target.name, board)
        target_store.record(
            target_id, BoardStatus.FOUND, clock(), board=target.board, slug=target.slug
        )
        return {"id": target_id, "offers": kept, "new": new}

    @app.post("/companies/{target_id}/board", dependencies=protected)
    def link_company_board(target_id: int, request: CareersLinkRequest) -> dict[str, Any]:
        """The user pasted the company's careers link: watch that board (D71)."""
        target = target_or_404(target_id)
        board = board_from_url(request.url)
        if board is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "That link is not on a job board this app reads. It reads Greenhouse, "
                "Lever, Ashby, Recruitee, Personio, SmartRecruiters and Workday "
                "(an address with myworkdayjobs.com).",
            )
        try:
            match = job_searcher.find_company(request.url)
        except (httpx.HTTPError, BoardError) as err:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"could not read that job board: {err}"
            ) from err
        if match is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No postings at {board.careers_url}. Is the link right?"
            )
        _, kept, new = job_searcher.watch_board(target.name, board)
        target_store.record(
            target_id, BoardStatus.FOUND, clock(), board=board.kind.value, slug=board.slug
        )
        return {"id": target_id, "board": board.kind.value, "offers": kept, "new": new}

    @app.post("/companies/{target_id}/reject", dependencies=protected)
    def reject_company_board(target_id: int) -> dict[str, Any]:
        """The possible board belongs to someone else."""
        target_or_404(target_id)
        target_store.record(target_id, BoardStatus.NONE, clock(), note="not theirs, you said")
        return {"id": target_id}

    @app.delete("/companies/{target_id}", dependencies=protected)
    def remove_company(target_id: int) -> dict[str, Any]:
        """Take it off the list. A board being watched stays on the Search page."""
        if not target_store.remove(target_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown company")
        return {"id": target_id}

    @app.get("/company", dependencies=protected, response_class=HTMLResponse)
    def company_view(name: str, context: str | None = None) -> HTMLResponse:
        """A company's news, hiring and whom to contact (D64)."""
        name, context = name.strip()[:100], (context or "").strip()[:100] or None
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a company name is needed")
        careers = [
            Board(BoardKind(c.board), c.slug).careers_url
            for c in search_store.list_companies()
            if similar_company(company_key(name), company_key(c.name))
            and c.board in {kind.value for kind in BoardKind}
        ]
        now = clock()
        views = offer_views(now, old_after())
        research = company_researcher.research(
            name, context, [v.stored for v in views], profile, careers
        )
        ids = {o.id for o in research.offers}
        return HTMLResponse(
            render_company(
                research,
                [v for v in views if v.stored.id in ids],
                can_summarize=can_summarize(),
                investigations=investigation_store.for_company(name),
                can_investigate=web_investigator() is not None and can_summarize(),
            )
        )

    def run_queue() -> None:
        # Already running: that job takes the new one before it stops.
        with suppress(SyncAlreadyRunningError):
            investigation_service.start()

    @app.post("/company/investigate", dependencies=protected, status_code=status.HTTP_202_ACCEPTED)
    def investigate_company(request: InvestigateRequest) -> dict[str, Any]:
        """Queue a web investigation of the company (D67)."""
        if web_investigator() is None or not can_summarize():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "web research needs SearXNG (APPLY_AGENT_SEARXNG_URL) and a model "
                "(APPLY_AGENT_LLM)",
            )
        investigation_id = investigation_store.queue(
            request.name, request.context, request.question
        )
        run_queue()
        return {"id": investigation_id, "status": "queued"}

    @app.get("/company/investigations/{investigation_id}", dependencies=protected)
    def investigation_status(investigation_id: int) -> dict[str, Any]:
        found = investigation_store.get(investigation_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown investigation")
        latest = investigation_service.latest()
        idle = latest is None or latest.status is not JobStatus.RUNNING
        if found.status is InvestigationStatus.QUEUED and idle:
            # Queued just as the last run was ending: start another.
            run_queue()
        return {"id": found.id, "status": found.status.value, "error": found.error}

    @app.post("/company/summary", dependencies=protected)
    def company_summary(request: SummaryRequest) -> dict[str, str]:
        """Have the configured model sum up the company's recent news."""
        news, _ = company_researcher.news(request.name, request.context)
        if summarizer is not None:
            return {"summary": summarizer(request.name, news)}
        model = text_model()
        if model is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "no model to summarize with: set APPLY_AGENT_LLM"
            )
        try:
            return {"summary": summarize(model, request.name, news)}
        except Exception as err:  # the page shows the failure; the app keeps running
            logger.exception("news summary failed")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"the model failed: {type(err).__name__}"
            ) from err

    @app.get("/search", dependencies=protected, response_class=HTMLResponse)
    def search_view(
        what: str | None = None,
        where: str | None = None,
        country: str = "es",
        company: str | None = None,
        max_days_old: int = 14,
    ) -> HTMLResponse:
        """The Search page. With ``what``, it searches every source for the country."""
        search: JobSearch | None = None
        results: list[OfferView] = []
        new = 0
        errors: list[str] = []
        searched: tuple[str, ...] = ()
        if what and what.strip():
            try:
                search = JobSearch.model_validate(
                    {
                        "what": what,
                        "where": where or None,
                        "country": country,
                        "company": company or None,
                        "max_days_old": max_days_old,
                    }
                )
            except ValidationError as err:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid search"
                ) from err
            try:
                result = job_searcher.search(search)
            except SearchUnavailableError as err:
                errors = [str(err)]
            else:
                results, new = views_for(result.offers, clock()), result.new
                errors, searched = list(result.errors), result.sources
        return HTMLResponse(
            render_search(
                search=search,
                results=results,
                new=new,
                errors=errors,
                searched=searched,
                sources=job_searcher.title_sources,
                saved=search_store.list_searches(),
                companies=search_store.list_companies(),
                default_keywords=profile.target,
            )
        )

    @app.post("/search/saved", dependencies=protected, status_code=status.HTTP_201_CREATED)
    def save_search(search: JobSearch) -> dict[str, Any]:
        """Run this search again on every sync."""
        return {"id": search_store.save_search(search)}

    @app.delete("/search/saved/{search_id}", dependencies=protected)
    def delete_search(search_id: int) -> dict[str, Any]:
        if not search_store.delete_search(search_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown saved search")
        return {"id": search_id}

    @app.post("/search/companies", dependencies=protected, status_code=status.HTTP_201_CREATED)
    def watch_company(request: WatchRequest) -> dict[str, Any]:
        """Find the company's job board, watch it, and read it once now.

        A board found by guessing that may be another company's answers 409
        with what it holds; sending ``confirm`` then watches it (D66).
        """
        try:
            match = job_searcher.find_company(request.name)
        except (httpx.HTTPError, BoardError) as err:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"could not reach the job boards: {err}"
            ) from err
        if match is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{request.name} was not found on any job board this app reads. "
                "Try the address of its careers page.",
            )
        board = match.board
        if not match.confirmed and not request.confirm:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Found {board.kind.value}/{board.slug} with {match.postings} posting(s), "
                f"such as: {'; '.join(match.examples)}. Is this {request.name}?",
            )
        name = request.name if board_from_url(request.name) is None else board.company_name
        company_id, kept, new = job_searcher.watch_board(
            name, board, request.keyword_list, request.place_list
        )
        return {
            "id": company_id,
            "board": board.kind.value,
            "slug": board.slug,
            "offers": kept,
            "new": new,
        }

    @app.delete("/search/companies/{company_id}", dependencies=protected)
    def unwatch_company(company_id: int) -> dict[str, Any]:
        """Stop reading its board. Offers already found stay."""
        if not search_store.unwatch_company(company_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown company")
        return {"id": company_id}

    return app

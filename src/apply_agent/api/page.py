"""The one server-rendered HTML page: applications grouped by stage.

Advancing applications come first, because they need the user; then the ones
sent and still waiting, with the quiet ones flagged; rejections last, folded.

Plain string rendering with ``html.escape`` on every value, so no template
engine dependency. Everything shown comes from email content or the LLM, so
it is treated as untrusted and always escaped.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Final
from urllib.parse import quote

from apply_agent.api.jobs import SyncJob
from apply_agent.domain import (
    Application,
    ApplicationStage,
    ApplicationStatus,
    Event,
    company_key,
    similar_company,
    stage_of,
)
from apply_agent.storage.repository import ApplicationView

QUIET_COLOUR: Final = "#b45309"
STATUS_COLOURS: Final = {
    "applied": "#64748b",
    "information_requested": "#b45309",
    "interviewing": "#1d4ed8",
    "offer_received": "#15803d",
    "rejected": "#b91c1c",
}

_STYLE: Final = """
:root { color-scheme: light dark; --muted: #6b7280; --line: #d1d5db; }
body {
  font: 15px/1.5 system-ui, sans-serif;
  max-width: 1100px;
  margin: 2rem auto;
  padding: 0 1rem;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
th, td {
  text-align: left;
  padding: .5rem .6rem;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
th { font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.status {
  color: #fff;
  border-radius: 999px;
  padding: .1rem .6rem;
  font-size: .8rem;
  white-space: nowrap;
}
.muted { color: var(--muted); font-size: .85rem; }
.date, th { white-space: nowrap; }
.emails { margin-top: .3rem; font-size: .85rem; }
.emails summary { margin: 0; color: var(--muted); }
.emails ul { margin: .3rem 0 0; padding-left: 1.2rem; }
.emails li { margin-bottom: .3rem; }
.category { color: var(--muted); white-space: nowrap; }
h2 { font-size: 1.15rem; margin: 2rem 0 0; }
summary h2 { display: inline; }
summary { cursor: pointer; margin-top: 2rem; }
.quiet { color: #b45309; font-weight: 600; }
.count { color: var(--muted); font-weight: normal; }
button, .button, select { font: inherit; padding: .4rem .9rem; cursor: pointer; }
.button {
  border: 1px solid var(--line);
  border-radius: 4px;
  text-decoration: none;
  color: inherit;
}
select { padding: .2rem .3rem; font-size: .85rem; width: 8.5rem; }
.actions { display: flex; gap: .5rem; align-items: center; }
.edited { font-size: .8rem; color: var(--muted); white-space: nowrap; }
.wrap { overflow-x: auto; }
"""

_SCRIPT: Final = """
async function sync(button) {
  button.disabled = true; button.textContent = "Syncing...";
  const started = await fetch("sync", {method: "POST"});
  const job = await started.json();
  const id = started.status === 409 ? job.running.id : job.id;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const state = await (await fetch("sync/" + id)).json();
    if (state.status !== "running") { location.reload(); return; }
  }
}
async function correct(id, select) {
  const value = select.value;
  if (!value) return;
  const json = {"Content-Type": "application/json"};
  let request;
  if (value === "auto") {
    request = ["applications/" + id + "/status", {method: "DELETE"}];
  } else if (value.startsWith("merge:")) {
    const label = select.selectedOptions[0].textContent;
    if (!confirm("Merge this row into: " + label + "? This cannot be undone.")) {
      select.value = ""; return;
    }
    const body = JSON.stringify({into: Number(value.slice(6))});
    request = ["applications/" + id + "/merge", {method: "POST", headers: json, body}];
  } else {
    const body = JSON.stringify({status: value});
    request = ["applications/" + id + "/status", {method: "PUT", headers: json, body}];
  }
  select.disabled = true;
  const response = await fetch(...request);
  if (response.ok) { location.reload(); return; }
  select.disabled = false; select.value = "";
  alert("Could not change this application.");
}
"""


@dataclass(frozen=True, slots=True)
class _Section:
    stage: ApplicationStage
    title: str
    empty: str
    folded: bool = False


_SECTIONS: Final = (
    _Section(ApplicationStage.ADVANCING, "Advancing", "Nothing moving yet."),
    _Section(ApplicationStage.SENT, "Sent, waiting for a reply", "No applications waiting."),
    _Section(ApplicationStage.REJECTED, "Rejected", "No rejections.", folded=True),
)


def days_since(when: datetime, now: datetime) -> int:
    return max((now - when).days, 0)


def _date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _ago(days: int) -> str:
    return "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"


def _possible_duplicates(app: Application, others: Sequence[Application]) -> list[Application]:
    """Other applications at a company with the same or a longer or shorter name."""
    key = company_key(app.company)
    return [o for o in others if o.id != app.id and similar_company(key, company_key(o.company))]


def _correction(view: ApplicationView, others: Sequence[Application]) -> str:
    """A menu to fix what the classifier got wrong: the status, or a duplicate row."""
    app = view.application
    options = "".join(
        f'<option value="{s.value}">{s.value.replace("_", " ")}</option>'
        for s in ApplicationStatus
        if s is not app.status
    )
    if app.status_overridden:
        options += '<option value="auto">automatic (undo)</option>'
    duplicates = "".join(
        f'<option value="merge:{o.id}">{escape(o.role or "no role")} ({escape(o.company)})</option>'
        for o in _possible_duplicates(app, others)
    )
    if duplicates:
        options = (
            f'<optgroup label="Status">{options}</optgroup>'
            f'<optgroup label="Same application as">{duplicates}</optgroup>'
        )
    return (
        f'<select aria-label="Correct {escape(app.company, quote=True)}" '
        f'onchange="correct({app.id}, this)">'
        f'<option value="">Change&hellip;</option>{options}</select>'
    )


@dataclass(frozen=True, slots=True)
class _Page:
    """What every row needs to know about the page it is on."""

    now: datetime
    quiet_after_days: int
    applications: Sequence[Application]
    gmail_links: bool


def gmail_url(message_id: str) -> str:
    """Where Gmail shows a message, given the id its API returned for it.

    It opens in the first signed-in account; with several accounts the user
    may have to switch. Only an id is put in the link, never email content.
    """
    return f"https://mail.google.com/mail/u/0/#all/{quote(message_id, safe='')}"


def _email(event: Event, *, gmail_links: bool) -> str:
    link = (
        f' <a href="{escape(gmail_url(event.message_id), quote=True)}" target="_blank" '
        'rel="noopener noreferrer">Open in Gmail</a>'
        if gmail_links
        else ""
    )
    return (
        f'<li><span class="date">{_date(event.occurred_at)}</span> '
        f'<span class="category">{escape(event.category.value.replace("_", " "))}</span>: '
        f"{escape(event.summary)}{link}</li>"
    )


def _emails(view: ApplicationView, *, gmail_links: bool) -> str:
    """The emails an application was built from, so its status can be checked."""
    count = len(view.events)
    if not count:
        return ""
    label = "1 email" if count == 1 else f"{count} emails"
    items = "".join(_email(e, gmail_links=gmail_links) for e in view.events)
    return f'<details class="emails"><summary>{label}</summary><ul>{items}</ul></details>'


def _row(view: ApplicationView, page: _Page) -> str:
    app = view.application
    colour = STATUS_COLOURS.get(app.status.value, "#64748b")
    days = days_since(app.last_activity_at, page.now)
    open_ = stage_of(app.status) is not ApplicationStage.REJECTED
    quiet = open_ and days >= page.quiet_after_days
    news = f'<span class="{"quiet" if quiet else "muted"}">{_ago(days)}</span>'
    edited = '<br><span class="edited">set by hand</span>' if app.status_overridden else ""
    return (
        "<tr>"
        f"<td>{escape(app.company)}</td>"
        f"<td>{escape(app.role or '-')}</td>"
        f'<td><span class="status" style="background:{colour}">'
        f"{escape(app.status.value.replace('_', ' '))}</span>{edited}</td>"
        f"<td>{escape(view.latest_summary)}{_emails(view, gmail_links=page.gmail_links)}</td>"
        f'<td class="date">{_date(app.first_seen_at)}</td>'
        f'<td class="date">{_date(app.last_activity_at)}<br>{news}</td>'
        f"<td>{_correction(view, page.applications)}</td>"
        "</tr>"
    )


_HEAD: Final = (
    "<thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Latest</th>"
    "<th>First seen</th><th>Last news</th><th>Correct</th></tr></thead>"
)


def _section(section: _Section, views: Sequence[ApplicationView], page: _Page) -> str:
    rows = "\n".join(_row(v, page) for v in views) or (
        f'<tr><td colspan="7" class="muted">{section.empty}</td></tr>'
    )
    heading = f'<h2>{section.title} <span class="count">({len(views)})</span></h2>'
    table = f'<div class="wrap"><table>{_HEAD}<tbody>\n{rows}\n</tbody></table></div>'
    body = f'<section id="{section.stage.value}">'
    if section.folded:
        return f"{body}<details><summary>{heading}</summary>{table}</details></section>"
    return f"{body}{heading}{table}</section>"


def _overview(groups: dict[ApplicationStage, list[ApplicationView]], quiet: int) -> str:
    parts = [
        f"{len(groups[ApplicationStage.ADVANCING])} advancing",
        f"{len(groups[ApplicationStage.SENT])} waiting",
        f"{len(groups[ApplicationStage.REJECTED])} rejected",
    ]
    if quiet:
        parts.append(f'<span class="quiet">{quiet} with no news lately</span>')
    return " &middot; ".join(parts)


def _last_sync(job: SyncJob | None) -> str:
    if job is None:
        return "No sync since the server started."
    when = job.started_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"Last sync: {escape(job.status.value)}, started {when}."


def render_applications(
    views: Sequence[ApplicationView],
    last_job: SyncJob | None,
    *,
    now: datetime,
    quiet_after_days: int,
    gmail_links: bool = False,
) -> str:
    groups: dict[ApplicationStage, list[ApplicationView]] = {s: [] for s in ApplicationStage}
    for view in views:
        groups[stage_of(view.application.status)].append(view)
    quiet = sum(
        1
        for stage in (ApplicationStage.ADVANCING, ApplicationStage.SENT)
        for v in groups[stage]
        if days_since(v.application.last_activity_at, now) >= quiet_after_days
    )
    page = _Page(now, quiet_after_days, [v.application for v in views], gmail_links)
    if views:
        sections = "\n".join(_section(s, groups[s.stage], page) for s in _SECTIONS)
    else:
        sections = '<p class="muted">No applications yet. Run a sync.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job applications</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
  <div>
    <h1>Job applications</h1>
    <div>{_overview(groups, quiet)}</div>
    <div class="muted">{len(views)} tracked. {_last_sync(last_job)}</div>
  </div>
  <div class="actions">
    <a class="button" href="applications?format=xlsx">Export to Excel</a>
    <button type="button" onclick="sync(this)">Sync now</button>
  </div>
</header>
{sections}
<script>{_SCRIPT}</script>
</body>
</html>
"""

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

from apply_agent.api.jobs import SyncJob
from apply_agent.domain import ApplicationStage, ApplicationStatus, stage_of
from apply_agent.storage.repository import ApplicationView

_STATUS_COLOURS: Final = {
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
  select.disabled = true;
  const url = "applications/" + id + "/status";
  const response = value === "auto"
    ? await fetch(url, {method: "DELETE"})
    : await fetch(url, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({status: value}),
      });
  if (response.ok) { location.reload(); return; }
  select.disabled = false; select.value = "";
  alert("Could not change the status.");
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


def _correction(view: ApplicationView) -> str:
    """A menu to set the status by hand, for when the classifier got it wrong."""
    app = view.application
    options = "".join(
        f'<option value="{s.value}">{s.value.replace("_", " ")}</option>'
        for s in ApplicationStatus
        if s is not app.status
    )
    if app.status_overridden:
        options += '<option value="auto">automatic (undo)</option>'
    return (
        f'<select aria-label="Correct the status of {escape(app.company, quote=True)}" '
        f'onchange="correct({app.id}, this)">'
        f'<option value="">Change&hellip;</option>{options}</select>'
    )


def _row(view: ApplicationView, now: datetime, quiet_after_days: int) -> str:
    app = view.application
    colour = _STATUS_COLOURS.get(app.status.value, "#64748b")
    days = days_since(app.last_activity_at, now)
    quiet = stage_of(app.status) is not ApplicationStage.REJECTED and days >= quiet_after_days
    news = f'<span class="{"quiet" if quiet else "muted"}">{_ago(days)}</span>'
    edited = '<br><span class="edited">set by hand</span>' if app.status_overridden else ""
    return (
        "<tr>"
        f"<td>{escape(app.company)}</td>"
        f"<td>{escape(app.role or '-')}</td>"
        f'<td><span class="status" style="background:{colour}">'
        f"{escape(app.status.value.replace('_', ' '))}</span>{edited}</td>"
        f"<td>{escape(view.latest_summary)}</td>"
        f"<td>{_date(app.first_seen_at)}</td>"
        f"<td>{_date(app.last_activity_at)}<br>{news}</td>"
        f"<td>{_correction(view)}</td>"
        "</tr>"
    )


_HEAD: Final = (
    "<thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Latest</th>"
    "<th>First seen</th><th>Last news</th><th>Correct</th></tr></thead>"
)


def _section(
    section: _Section, views: Sequence[ApplicationView], now: datetime, quiet_after_days: int
) -> str:
    rows = "\n".join(_row(v, now, quiet_after_days) for v in views) or (
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
    if views:
        sections = "\n".join(_section(s, groups[s.stage], now, quiet_after_days) for s in _SECTIONS)
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

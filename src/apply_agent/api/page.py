"""The one server-rendered HTML page: the applications table.

Plain string rendering with ``html.escape`` on every value, so no template
engine dependency. Everything shown comes from email content or the LLM, so
it is treated as untrusted and always escaped.
"""

from collections.abc import Sequence
from datetime import datetime
from html import escape
from typing import Final

from apply_agent.api.jobs import SyncJob
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
button { font: inherit; padding: .4rem .9rem; cursor: pointer; }
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
"""


def _date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _row(view: ApplicationView) -> str:
    app = view.application
    colour = _STATUS_COLOURS.get(app.status.value, "#64748b")
    return (
        "<tr>"
        f"<td>{escape(app.company)}</td>"
        f"<td>{escape(app.role or '-')}</td>"
        f'<td><span class="status" style="background:{colour}">'
        f"{escape(app.status.value.replace('_', ' '))}</span></td>"
        f"<td>{escape(view.latest_summary)}</td>"
        f"<td>{_date(app.first_seen_at)}</td>"
        f"<td>{_date(app.last_activity_at)}</td>"
        "</tr>"
    )


def _last_sync(job: SyncJob | None) -> str:
    if job is None:
        return "No sync since the server started."
    when = job.started_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"Last sync: {escape(job.status.value)}, started {when}."


def render_applications(views: Sequence[ApplicationView], last_job: SyncJob | None) -> str:
    rows = "\n".join(_row(v) for v in views) or (
        '<tr><td colspan="6" class="muted">No applications yet. Run a sync.</td></tr>'
    )
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
    <div class="muted">{len(views)} tracked. {_last_sync(last_job)}</div>
  </div>
  <button type="button" onclick="sync(this)">Sync now</button>
</header>
<div class="wrap">
<table>
  <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Latest</th>
  <th>First seen</th><th>Last activity</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
</div>
<script>{_SCRIPT}</script>
</body>
</html>
"""

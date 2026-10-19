"""The job offers page: postings found on LinkedIn, and whether you applied.

Offers you have not applied to come first, the ones you marked as
interesting at the top; those you already applied to and those you dismissed
are folded at the end. Like the applications page, every value comes from
outside (an email or a web page) and is escaped.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Final
from urllib.parse import quote_plus

from apply_agent.api.jobs import SyncJob
from apply_agent.api.page import SEARCH_SCRIPT, STATUS_COLOURS, STYLE, nav
from apply_agent.domain.offers import (
    OfferApplicationMatch,
    OfferMatch,
    OfferSource,
    OfferState,
    StoredOffer,
)

_SCRIPT: Final = """
async function scan(button) {
  button.disabled = true; button.textContent = "Searching...";
  const started = await fetch("offers/scan", {method: "POST"});
  const job = await started.json();
  const id = started.status === 409 ? job.running.id : job.id;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const state = await (await fetch("offers/scan/" + id)).json();
    if (state.status !== "running") { location.reload(); return; }
  }
}
async function decide(id, select) {
  if (!select.value) return;
  select.disabled = true;
  const response = await fetch("offers/" + id + "/state", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({state: select.value}),
  });
  if (response.ok) { location.reload(); return; }
  select.disabled = false; select.value = "";
  alert("Could not change this offer.");
}
"""

_SOURCES: Final = {
    OfferSource.LINKEDIN_ALERT: "job alert",
    OfferSource.LINKEDIN_SEARCH: "LinkedIn search",
}
_ACTIONS: Final = {
    OfferState.INTERESTED: "Interested",
    OfferState.DISMISSED: "Dismiss",
    OfferState.NEW: "Back to new",
}


@dataclass(frozen=True, slots=True)
class OfferView:
    stored: StoredOffer
    match: OfferApplicationMatch


@dataclass(frozen=True, slots=True)
class _Section:
    key: str
    title: str
    empty: str
    folded: bool = False


_SECTIONS: Final = (
    _Section("interested", "Interested", "Nothing marked as interesting."),
    _Section("new", "New offers", "No new offers. Set up LinkedIn job alerts, then search."),
    _Section("applied", "Already applied", "None of these offers matches an application.", True),
    _Section("dismissed", "Dismissed", "Nothing dismissed.", folded=True),
)


def section_of(view: OfferView) -> str:
    if view.stored.state is OfferState.DISMISSED:
        return "dismissed"
    if view.match.kind is OfferMatch.APPLIED:
        return "applied"
    return "interested" if view.stored.state is OfferState.INTERESTED else "new"


def offer_link(view: OfferView) -> str:
    """The posting itself, or a LinkedIn search for it when the alert gave no link."""
    offer = view.stored.offer
    if offer.url is not None:
        return offer.url
    terms = quote_plus(f"{offer.title} {offer.company}")
    return f"https://www.linkedin.com/jobs/search/?keywords={terms}"


def _status(match: OfferApplicationMatch) -> str:
    if match.application_status is None:
        return ""
    colour = STATUS_COLOURS.get(match.application_status.value, "#64748b")
    label = escape(match.application_status.value.replace("_", " "))
    return f' <span class="status" style="background:{colour}">{label}</span>'


def _match(match: OfferApplicationMatch) -> str:
    if match.kind is OfferMatch.APPLIED:
        return f'<span class="applied">Applied</span>{_status(match)}'
    if match.kind is OfferMatch.SAME_COMPANY:
        role = escape(match.application_role or "a role not stated")
        return f'<span class="match muted">You applied here for {role}</span>{_status(match)}'
    return '<span class="muted">-</span>'


def _actions(view: OfferView) -> str:
    stored = view.stored
    options = "".join(
        f'<option value="{state.value}">{label}</option>'
        for state, label in _ACTIONS.items()
        if state is not stored.state
    )
    company = escape(stored.offer.company, quote=True)
    return (
        f'<select aria-label="Decide about {company}" onchange="decide({stored.id}, this)">'
        f'<option value="">Change&hellip;</option>{options}</select>'
    )


def _row(view: OfferView) -> str:
    offer = view.stored.offer
    search = escape(f"{offer.company} {offer.title} {offer.location or ''}", quote=True)
    link = escape(offer_link(view), quote=True)
    posted = offer.posted_on.isoformat() if offer.posted_on else "-"
    return (
        f'<tr data-search="{search}">'
        f'<td><a href="{link}" target="_blank" rel="noopener noreferrer">'
        f"{escape(offer.title)}</a></td>"
        f"<td>{escape(offer.company)}</td>"
        f"<td>{escape(offer.location or '-')}</td>"
        f'<td class="date">{view.stored.first_seen_at:%Y-%m-%d}<br>'
        f'<span class="muted">{_SOURCES[offer.source]}</span></td>'
        f'<td class="date">{posted}</td>'
        f"<td>{_match(view.match)}</td>"
        f"<td>{_actions(view)}</td>"
        "</tr>"
    )


_HEAD: Final = (
    "<thead><tr><th>Role</th><th>Company</th><th>Location</th><th>Found</th>"
    "<th>Posted</th><th>Your applications</th><th>Decide</th></tr></thead>"
)


def _section(section: _Section, views: Sequence[OfferView]) -> str:
    rows = "\n".join(_row(v) for v in views) or (
        f'<tr><td colspan="7" class="muted">{section.empty}</td></tr>'
    )
    heading = f'<h2>{section.title} <span class="count">({len(views)})</span></h2>'
    table = f'<div class="wrap"><table>{_HEAD}<tbody>\n{rows}\n</tbody></table></div>'
    body = f'<section id="{section.key}">'
    if section.folded:
        return f"{body}<details><summary>{heading}</summary>{table}</details></section>"
    return f"{body}{heading}{table}</section>"


def _last_scan(job: SyncJob | None) -> str:
    if job is None:
        return "No search since the server started."
    when = job.started_at.strftime("%Y-%m-%d %H:%M UTC")
    text = f"Last search: {escape(job.status.value)}, started {when}."
    errors = (job.report or {}).get("search_errors") or []
    if errors:
        text += f' <span class="quiet">{len(errors)} LinkedIn search(es) failed.</span>'
    return text


def render_offers(
    views: Sequence[OfferView],
    last_job: SyncJob | None,
    *,
    search_enabled: bool,
) -> str:
    groups: dict[str, list[OfferView]] = {s.key: [] for s in _SECTIONS}
    for view in views:
        groups[section_of(view)].append(view)
    overview = " &middot; ".join(
        (
            f"{len(groups['new'])} new",
            f"{len(groups['interested'])} interested",
            f"{len(groups['applied'])} already applied",
        )
    )
    source = (
        "Offers come from LinkedIn job alert emails and LinkedIn's public search."
        if search_enabled
        else "Offers come from LinkedIn job alert emails. LinkedIn search is off."
    )
    if views:
        sections = "\n".join(_section(s, groups[s.key]) for s in _SECTIONS)
    else:
        sections = (
            '<p class="muted">No offers yet. Create job alerts on LinkedIn, then press '
            "<em>Find offers</em>.</p>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job offers</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <div>
    {nav("offers")}
    <h1>Job offers</h1>
    <div>{overview}</div>
    <div class="muted">{source} {_last_scan(last_job)}</div>
  </div>
  <div class="actions">
    <input type="search" id="search" placeholder="Search role, company or place"
           aria-label="Search offers" oninput="filterRows(this)">
    <button type="button" onclick="scan(this)">Find offers</button>
  </div>
</header>
{sections}
<p id="no-match" class="muted" hidden>No offers match the search.</p>
<script>{_SCRIPT}{SEARCH_SCRIPT}
filterRows(document.getElementById("search"));</script>
</body>
</html>
"""

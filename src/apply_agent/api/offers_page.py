"""The job offers page: postings found on LinkedIn, and whether you applied.

Offers you have not applied to come first, ranked by fit and date, the ones
you marked as interesting at the top. Poor fits, old offers, those you
already applied to and those you dismissed are folded at the end. Like the
applications page, every value comes from outside (an email or a web page)
and is escaped.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Final
from urllib.parse import quote_plus

from apply_agent.api.jobs import SyncJob
from apply_agent.api.page import SEARCH_SCRIPT, STATUS_COLOURS, STYLE, SYNC_SCRIPT, days_since, nav
from apply_agent.domain.fit import FitLevel, OfferFit
from apply_agent.domain.offers import (
    MIN_CLOSURES_TO_LEARN,
    OfferApplicationMatch,
    OfferMatch,
    OfferSource,
    OfferState,
    StoredOffer,
)

_SCRIPT: Final = """
async function decide(id, select) {
  if (!select.value) return;
  select.disabled = true;
  let request;
  if (select.value === "closed") {
    request = ["offers/" + id + "/closed", {method: "PUT"}];
  } else if (select.value === "reopen") {
    request = ["offers/" + id + "/closed", {method: "DELETE"}];
  } else {
    request = ["offers/" + id + "/state", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({state: select.value}),
    }];
  }
  const response = await fetch(...request);
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
    fit: OfferFit
    # No alert has mentioned it for longer than the user's "old" threshold.
    old: bool = False


@dataclass(frozen=True, slots=True)
class OldAfter:
    """After how many days without an alert an offer folds away, and why (D61)."""

    days: int
    closures: int = 0
    learned: bool = False


@dataclass(frozen=True, slots=True)
class _Section:
    key: str
    title: str
    empty: str
    folded: bool = False


_SECTIONS: Final = (
    _Section("interested", "Interested", "Nothing marked as interesting."),
    _Section("new", "New offers", "No new offers. Set up LinkedIn job alerts, then sync."),
    _Section("poor", "Poor fit", "No offers ranked as a poor fit.", folded=True),
    _Section("older", "Older offers", "No old offers.", folded=True),
    _Section("applied", "Already applied", "None of these offers matches an application.", True),
    _Section("closed", "No longer available", "No offer marked as closed.", folded=True),
    _Section("dismissed", "Dismissed", "Nothing dismissed.", folded=True),
)


def section_of(view: OfferView) -> str:
    if view.stored.state is OfferState.DISMISSED:
        return "dismissed"
    if view.match.kind is OfferMatch.APPLIED:
        return "applied"
    if view.stored.closed_at is not None:
        return "closed"
    if view.stored.state is OfferState.INTERESTED:
        return "interested"
    if view.old:
        return "older"
    return "poor" if view.fit.level is FitLevel.UNLIKELY else "new"


def rank(view: OfferView) -> tuple[int, float]:
    """Best fit first; among equals, the offer an alert mentioned most recently.

    The page groups offers into sections and ranks each; the JSON is one ranked list.
    """
    return (-view.fit.score, -view.stored.last_seen_at.timestamp())


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
    if stored.closed_at is None:
        options += '<option value="closed">No longer available</option>'
    else:
        options += '<option value="reopen">Still open (undo)</option>'
    company = escape(stored.offer.company, quote=True)
    return (
        f'<select aria-label="Decide about {company}" onchange="decide({stored.id}, this)">'
        f'<option value="">Change&hellip;</option>{options}</select>'
    )


_FIT_COLOURS: Final = {
    FitLevel.GOOD: "#15803d",
    FitLevel.POSSIBLE: "#b45309",
    FitLevel.UNLIKELY: "#6b7280",
}


def _fit(fit: OfferFit) -> str:
    """A badge; the reasons are on hover, so the column stays narrow."""
    why = escape("; ".join(fit.reasons), quote=True)
    return (
        f'<span class="status" style="background:{_FIT_COLOURS[fit.level]}" title="{why}">'
        f"{fit.level.value}</span><br>"
        f'<span class="muted">{fit.score}/100</span>'
    )


FRESH_DAYS: Final = 2


def _age(days: int, fold_days: int) -> tuple[str, str]:
    """A label and a class: fresh in green, past half the fold in orange (D61)."""
    ago = "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"
    if days <= FRESH_DAYS:
        return f"new, {ago}", "fresh"
    if days * 2 > fold_days:
        return f"{ago}, may be closed", "quiet"
    return ago, "muted"


def _found(view: OfferView, now: datetime, fold_days: int) -> str:
    """When an alert last mentioned it: the newer, the likelier it is still open."""
    stored = view.stored
    label, css = _age(days_since(stored.last_seen_at, now), fold_days)
    posted = stored.offer.posted_on
    extra = f"<br>posted {posted.isoformat()}" if posted else ""
    closed = (
        f'<br><span class="muted">closed by {stored.closed_at:%Y-%m-%d}</span>'
        if stored.closed_at
        else ""
    )
    return (
        f'<td class="date"><span class="{css}">{label}</span><br>'
        f'<span class="muted">{stored.last_seen_at:%Y-%m-%d}, '
        f"{_SOURCES[stored.offer.source]}{extra}</span>{closed}</td>"
    )


def _row(view: OfferView, now: datetime, fold_days: int) -> str:
    offer = view.stored.offer
    search = escape(f"{offer.company} {offer.title} {offer.location or ''}", quote=True)
    link = escape(offer_link(view), quote=True)
    return (
        f'<tr data-search="{search}">'
        f"<td>{_fit(view.fit)}</td>"
        f'<td><a href="{link}" target="_blank" rel="noopener noreferrer">'
        f"{escape(offer.title)}</a></td>"
        f"<td>{escape(offer.company)}</td>"
        f"<td>{escape(offer.location or '-')}</td>"
        f"{_found(view, now, fold_days)}"
        f"<td>{_match(view.match)}</td>"
        f"<td>{_actions(view)}</td>"
        "</tr>"
    )


_HEAD: Final = (
    "<thead><tr><th>Fit</th><th>Role</th><th>Company</th><th>Location</th><th>Last seen</th>"
    "<th>Your applications</th><th>Decide</th></tr></thead>"
)


def _section(section: _Section, views: Sequence[OfferView], now: datetime, fold_days: int) -> str:
    rows = "\n".join(_row(v, now, fold_days) for v in sorted(views, key=rank)) or (
        f'<tr><td colspan="7" class="muted">{section.empty}</td></tr>'
    )
    heading = f'<h2>{section.title} <span class="count">({len(views)})</span></h2>'
    table = f'<div class="wrap"><table>{_HEAD}<tbody>\n{rows}\n</tbody></table></div>'
    body = f'<section id="{section.key}">'
    if section.folded:
        return f"{body}<details><summary>{heading}</summary>{table}</details></section>"
    return f"{body}{heading}{table}</section>"


def _last_sync(job: SyncJob | None) -> str:
    """The shared sync, and what went wrong with its offers part, if anything."""
    if job is None:
        return "No sync since the server started."
    when = job.started_at.strftime("%Y-%m-%d %H:%M UTC")
    text = f"Last sync: {escape(job.status.value)}, started {when}."
    offers = (job.report or {}).get("offers") or {}
    if offers.get("error"):
        text += ' <span class="quiet">Looking for offers failed.</span>'
    errors = offers.get("search_errors") or []
    if errors:
        text += f' <span class="quiet">{len(errors)} LinkedIn search(es) failed.</span>'
    return text


def _fold_note(old_after: OldAfter) -> str:
    days = old_after.days
    if old_after.learned:
        return (
            f"Offers fold into <em>Older offers</em> {days} days after their last alert: "
            f"the typical age of the {old_after.closures} offers you found closed."
        )
    missing = MIN_CLOSURES_TO_LEARN - old_after.closures
    return (
        f"Offers fold into <em>Older offers</em> {days} days after their last alert. "
        f"Mark {missing} more as <em>No longer available</em> to learn when they usually close."
    )


def render_offers(
    views: Sequence[OfferView],
    last_job: SyncJob | None,
    *,
    search_enabled: bool,
    now: datetime,
    old_after: OldAfter,
) -> str:
    groups: dict[str, list[OfferView]] = {s.key: [] for s in _SECTIONS}
    for view in views:
        groups[section_of(view)].append(view)
    good = sum(1 for v in groups["new"] if v.fit.level is FitLevel.GOOD)
    overview = " &middot; ".join(
        (
            f"{len(groups['new'])} new",
            f"{good} a good fit",
            f"{len(groups['interested'])} interested",
            f"{len(groups['poor'])} poor fit",
            f"{len(groups['applied'])} already applied",
            f"{len(groups['older'])} older than {old_after.days} days",
            f"{len(groups['closed'])} no longer available",
        )
    )
    source = (
        "Offers come from LinkedIn job alert emails and LinkedIn's public search."
        if search_enabled
        else "Offers come from LinkedIn job alert emails. LinkedIn search is off."
    )
    if views:
        sections = "\n".join(_section(s, groups[s.key], now, old_after.days) for s in _SECTIONS)
    else:
        sections = (
            '<p class="muted">No offers yet. Create job alerts on LinkedIn, then press '
            "<em>Sync now</em>.</p>"
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
    <div class="muted">{source} {_last_sync(last_job)}</div>
    <div class="muted">{_fold_note(old_after)}</div>
  </div>
  <div class="actions">
    <input type="search" id="search" placeholder="Search role, company or place"
           aria-label="Search offers" oninput="filterRows(this)">
    <button type="button" onclick="sync(this)">Sync now</button>
  </div>
</header>
{sections}
<p id="no-match" class="muted" hidden>No offers match the search.</p>
<script>{SYNC_SCRIPT}{_SCRIPT}{SEARCH_SCRIPT}
filterRows(document.getElementById("search"));</script>
</body>
</html>
"""

"""The Companies page: the user's list of companies, each with its breakdown (D66).

A list pasted in one go (one group per line) is checked in the background
for job boards this app reads. Each company then shows what can be known
about it: its board, its open offers and how well they fit, and links to
its company page (news, contacts) and to LinkedIn. Every value is escaped.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Final
from urllib.parse import quote_plus

from apply_agent.api.jobs import JobStatus, SyncJob
from apply_agent.api.offers_page import OfferView, fit_badge, offer_link, rank
from apply_agent.api.page import STYLE, company_link, company_url, nav
from apply_agent.domain.fit import FitLevel
from apply_agent.domain.keys import company_key
from apply_agent.domain.places import country_of, places_of
from apply_agent.domain.search import BoardStatus, TargetCompany
from apply_agent.offers.boards import Board, BoardKind

_BOARD_NAMES: Final = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "lever_eu": "Lever (EU)",
    "ashby": "Ashby",
    "recruitee": "Recruitee",
    "personio": "Personio",
    "personio_com": "Personio",
    "smartrecruiters": "SmartRecruiters",
}

_SCRIPT: Final = """
const json = {"Content-Type": "application/json"};
async function post(url, method, body, button) {
  if (button) button.disabled = true;
  const response = await fetch(url, {method, headers: json, body: body && JSON.stringify(body)});
  if (!response.ok) {
    if (button) button.disabled = false;
    const detail = (await response.json().catch(() => ({}))).detail;
    alert(typeof detail === "string" ? detail : "That did not work.");
    return null;
  }
  return response.json();
}
async function waitFor(job) {
  const status = document.getElementById("checking");
  status.hidden = false;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const state = await (await fetch("companies/check/" + job)).json();
    if (state.status !== "running") { location.reload(); return; }
  }
}
async function importList(form) {
  const button = form.querySelector("button");
  const result = await post("companies/import", "POST", {text: form.text.value}, button);
  if (result) waitFor(result.job.id);
}
async function checkAgain(button) {
  const result = await post("companies/check", "POST", null, button);
  if (result) waitFor(result.id);
}
async function act(url, method, button) {
  if (await post(url, method, null, button)) location.reload();
}
"""


@dataclass(frozen=True, slots=True)
class CompanyRow:
    """One company on the list and what is known about it."""

    target: TargetCompany
    views: tuple[OfferView, ...]
    new_this_week: int


def _board(row: CompanyRow) -> str:
    target = row.target
    name = _BOARD_NAMES.get(target.board or "", escape(target.board or ""))
    linkedin = "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(target.name)
    match target.status:
        case BoardStatus.FOUND if target.board and target.slug:
            url = Board(BoardKind(target.board), target.slug).careers_url
            return (
                f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{name}</a>'
            )
        case BoardStatus.POSSIBLE:
            return (
                f'<span class="quiet">Maybe {name} ({escape(target.slug or "")})</span><br>'
                f'<span class="muted">{escape(target.note or "")}</span><br>'
                f'<button type="button" onclick="act(\'companies/{target.id}/confirm\', '
                f"'POST', this)\">It's them</button> "
                f'<button type="button" onclick="act(\'companies/{target.id}/reject\', '
                f"'POST', this)\">Not them</button>"
            )
        case BoardStatus.PENDING:
            return '<span class="muted">Looking for its job board...</span>'
        case BoardStatus.ERROR:
            return f'<span class="quiet">Failed: {escape(target.note or "")}</span>'
        case _:
            return (
                '<span class="muted">Not on a board this app reads. Follow it with a '
                "LinkedIn job alert for the company:</span> "
                f'<a href="{escape(linkedin, quote=True)}" target="_blank" '
                'rel="noopener noreferrer">its jobs on LinkedIn</a>'
            )


MAX_PLACES: Final = 3


def _where(views: Sequence[OfferView], market: str | None) -> str:
    """'2 in Spain · 1 in Switzerland', the group's own market in bold (D68).

    An offer in several countries counts in each; a place with no known
    country is shown as written.
    """
    counts: Counter[str] = Counter()
    for view in views:
        places = places_of(view.stored.offer.location)
        counts.update({country or place for place, country in places} or {"place not given"})
    shown = []
    for place, count in counts.most_common(MAX_PLACES):
        text = f"{count} in {escape(place)}"
        shown.append(f"<strong>{text}</strong>" if place == market else text)
    more = len(counts) - MAX_PLACES
    return " &middot; ".join(shown) + (f" &middot; {more} more" if more > 0 else "")


def _other_names(target: TargetCompany, views: Sequence[OfferView]) -> str:
    """Names the offers give that are not the company's own, such as 'Siemens Energy'."""
    key = company_key(target.name)
    names = Counter(
        v.stored.offer.company for v in views if company_key(v.stored.offer.company) != key
    )
    if not names:
        return ""
    listed = ", ".join(f"{escape(name)} ({count})" for name, count in names.most_common())
    return f'<br><span class="quiet">Includes {listed}</span>'


def _breakdown(row: CompanyRow) -> str:
    open_views = [v for v in row.views if v.stored.closed_at is None]
    if not open_views:
        return '<td class="muted">-</td><td class="muted">-</td><td class="muted">-</td>'
    good = sum(1 for v in open_views if v.fit.level is FitLevel.GOOD)
    best = sorted(open_views, key=rank)[0]
    offer = best.stored.offer
    link = escape(offer_link(best), quote=True)
    other = (
        f" at {escape(offer.company)}"
        if company_key(offer.company) != company_key(row.target.name)
        else ""
    )
    place = f'<br><span class="muted">{escape(offer.location)}</span>' if offer.location else ""
    market = country_of(row.target.group)
    return (
        f"<td><strong>{len(open_views)}</strong> open<br>"
        f'<span class="muted">{row.new_this_week} new this week</span><br>'
        f'<span class="muted">{_where(open_views, market)}</span>'
        f"{_other_names(row.target, open_views)}</td>"
        f"<td>{good}</td>"
        f'<td>{fit_badge(best.fit)} <a href="{link}" target="_blank" '
        f'rel="noopener noreferrer">{escape(offer.title)}</a>{other}{place}</td>'
    )


def _row(row: CompanyRow) -> str:
    target = row.target
    research = escape(company_url(target.name, target.context), quote=True)
    return (
        f'<tr data-search="{escape(target.name, quote=True)}">'
        f"<td>{company_link(target.name)}</td>"
        f"<td>{_board(row)}</td>"
        f"{_breakdown(row)}"
        f'<td><a href="{research}">News and contacts</a><br>'
        f"<button type=\"button\" onclick=\"act('companies/{target.id}', 'DELETE', this)\">"
        "Remove</button></td></tr>"
    )


_HEAD: Final = (
    "<thead><tr><th>Company</th><th>Job board</th><th>Offers</th><th>Good fits</th>"
    "<th>Best match</th><th></th></tr></thead>"
)


def _groups(rows: Sequence[CompanyRow]) -> str:
    grouped: dict[str, list[CompanyRow]] = {}
    for row in rows:
        grouped.setdefault(row.target.group or "Other", []).append(row)
    return "\n".join(
        f'<section><h2>{escape(group)} <span class="count">({len(items)})</span></h2>'
        f'<div class="wrap"><table>{_HEAD}<tbody>{"".join(_row(r) for r in items)}'
        "</tbody></table></div></section>"
        for group, items in grouped.items()
    )


def _summary(rows: Sequence[CompanyRow], job: SyncJob | None) -> str:
    counts = dict.fromkeys(BoardStatus, 0)
    for row in rows:
        counts[row.target.status] += 1
    parts = [
        f"{len(rows)} companies",
        f"{counts[BoardStatus.FOUND]} with a job board this app reads",
        f"{counts[BoardStatus.POSSIBLE]} to confirm",
        f"{counts[BoardStatus.NONE]} followed through LinkedIn alerts only",
    ]
    if counts[BoardStatus.PENDING] or counts[BoardStatus.ERROR]:
        parts.append(f"{counts[BoardStatus.PENDING] + counts[BoardStatus.ERROR]} still to check")
    running = job is not None and job.status is JobStatus.RUNNING
    return " &middot; ".join(parts) + (" &middot; checking now..." if running else "")


def render_companies(rows: Sequence[CompanyRow], job: SyncJob | None) -> str:
    running = job is not None and job.status is JobStatus.RUNNING
    table = _groups(rows) if rows else '<p class="muted">No companies yet. Paste a list above.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Companies</title>
<style>{STYLE}
textarea {{ font: inherit; width: 100%; max-width: 46rem; min-height: 6rem; padding: .5rem; }}
</style>
</head>
<body>
<header>
  <div>
    {nav("companies")}
    <h1>Companies</h1>
    <div>{_summary(rows, job)}</div>
  </div>
  <div class="actions">
    <button type="button" onclick="checkAgain(this)">Check job boards again</button>
  </div>
</header>
<section id="import">
<h2>Add companies</h2>
<form onsubmit="importList(this); return false">
  <textarea name="text" required placeholder="Netherlands: ASML, Booking.com, Adyen y ING
London: Revolut, Wise, Monzo"></textarea><br>
  <button type="submit">Add and look for their job boards</button>
</form>
<p class="muted">One group per line, before a colon; names separated by commas or "and".
Each company takes a few seconds to check.</p>
<p id="checking" class="quiet"{"" if running else " hidden"}>Looking for job boards...
this page reloads when done.</p>
</section>
{table}
<script>{_SCRIPT}{"waitFor(" + repr(job.id) + ");" if running and job else ""}</script>
</body>
</html>
"""

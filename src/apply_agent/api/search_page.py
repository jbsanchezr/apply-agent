"""The Search page: search by title, save searches, watch companies' boards (D62).

The form sends a GET, so a search can be bookmarked or reloaded. Its results
are offers like any other: they get the fit score and the applied check, and
appear on the Offers page, where they can be marked interesting or dismissed.
Every value shown comes from outside and is escaped.
"""

from collections.abc import Sequence
from html import escape
from typing import Final

from apply_agent.api.offers_page import OfferView, fit_badge, match_cell, offer_link, rank
from apply_agent.api.page import LINKEDIN_GEO_IDS, STYLE, company_link, linkedin_jobs_url, nav
from apply_agent.domain.search import COUNTRY_NAMES, JobSearch, SavedSearch, WatchedCompany
from apply_agent.offers.search import TitleSource

_SCRIPT: Final = """
const json = {"Content-Type": "application/json"};
async function send(url, method, body, button) {
  if (button) button.disabled = true;
  const response = await fetch(url, {method, headers: json, body: body && JSON.stringify(body)});
  if (response.ok) { location.reload(); return; }
  if (button) button.disabled = false;
  const detail = (await response.json().catch(() => ({}))).detail;
  alert(typeof detail === "string" ? detail : "That did not work.");
}
// Results: order by fit or by posting date, ties broken by the other.
function sortResults(by) {
  const body = document.querySelector("#result-table tbody");
  const other = by === "fit" ? "posted" : "fit";
  const value = (row, key) => key === "fit" ? Number(row.dataset.score) : row.dataset.posted;
  const compare = (a, b, key) => value(a, key) < value(b, key) ? 1
    : value(a, key) > value(b, key) ? -1 : 0;
  [...body.rows].sort((a, b) => compare(a, b, by) || compare(a, b, other))
    .forEach(row => body.appendChild(row));
}
function hidePoor(hide) {
  for (const row of document.querySelectorAll("#result-table tbody tr")) {
    row.hidden = hide && row.dataset.level === "unlikely";
  }
}
// The same search on LinkedIn, in the place and country chosen right now.
function linkedInLink(form) {
  const url = new URL("https://www.linkedin.com/jobs/search-results/");
  url.searchParams.set("keywords", form.what.value.trim());
  const option = form.country.selectedOptions[0];
  const where = form.where.value.trim();
  // LinkedIn follows the country's id and ignores a written place (D72).
  const country = option.textContent;
  if (option.dataset.geo) url.searchParams.set("geoId", option.dataset.geo);
  else url.searchParams.set("location", where ? `${where}, ${country}` : country);
  document.getElementById("linkedin").href = url;
}
// A LinkedIn results page pasted by the user (D73). Its HTML is sent to the
// app and never put into this page: what comes back is shown as text.
let pastedHtml = "";
async function readPaste(save) {
  const response = await fetch("offers/paste", {
    method: "POST", headers: json, body: JSON.stringify({html: pastedHtml, save}),
  });
  if (response.ok) return response.json();
  const detail = (await response.json().catch(() => ({}))).detail;
  alert(typeof detail === "string" ? detail : "That did not work.");
  return null;
}
function cell(row, text, href) {
  const td = row.insertCell();
  if (href) {
    const a = Object.assign(document.createElement("a"), {href, textContent: text});
    a.target = "_blank"; a.rel = "noopener noreferrer"; td.append(a);
  } else td.textContent = text;
}
async function pasted(event) {
  event.preventDefault();
  const box = event.target, out = document.getElementById("paste-result");
  out.replaceChildren();
  pastedHtml = event.clipboardData.getData("text/html");
  if (!pastedHtml) {
    box.value = "That paste had no links. Copy the LinkedIn page itself (Ctrl+A, Ctrl+C).";
    return;
  }
  box.value = "Reading the page...";
  const result = await readPaste(false);
  if (!result) { box.value = ""; return; }
  const fresh = result.offers.filter(o => !o.known).length;
  box.value = `${result.offers.length} offers on the page, ${fresh} new to you` +
    (result.unreadable ? `; ${result.unreadable} could not be read.` : ".");
  if (!result.offers.length) return;
  const table = document.createElement("table");
  table.createTHead().insertRow().append(...["Fit", "Role", "Company", "Location", ""].map(
    name => Object.assign(document.createElement("th"), {textContent: name})));
  const body = table.createTBody();
  for (const offer of result.offers) {
    const row = body.insertRow();
    cell(row, `${offer.fit} ${offer.fit_score}`);
    cell(row, offer.title, offer.url);
    cell(row, offer.company);
    cell(row, offer.location || "-");
    cell(row, offer.known ? "already in your offers" : "");
  }
  const add = Object.assign(document.createElement("button"), {
    type: "button", textContent: `Add them to Job offers (${fresh} new)`,
  });
  add.onclick = async () => {
    add.disabled = true;
    const saved = await readPaste(true);
    add.textContent = saved ? `Added: ${saved.new} new` : "Add them to Job offers";
    add.disabled = Boolean(saved);
  };
  const wrap = Object.assign(document.createElement("div"), {className: "wrap"});
  wrap.append(table);
  out.append(add, wrap);
}
function saveSearch(button) {
  send("search/saved", "POST", JSON.parse(button.dataset.search), button);
}
async function watch(form) {
  const data = Object.fromEntries(new FormData(form));
  const button = form.querySelector("button");
  button.disabled = true; button.textContent = "Looking for its job board...";
  let response = await fetch("search/companies", {
    method: "POST", headers: json, body: JSON.stringify(data),
  });
  if (response.status === 409) {
    // A board under its name that may belong to someone else: ask (D66).
    const detail = (await response.json()).detail;
    if (confirm(detail)) {
      response = await fetch("search/companies", {
        method: "POST", headers: json, body: JSON.stringify({...data, confirm: true}),
      });
    } else {
      button.disabled = false; button.textContent = "Watch"; return false;
    }
  }
  if (response.ok) { location.reload(); return false; }
  button.disabled = false; button.textContent = "Watch";
  const detail = (await response.json().catch(() => ({}))).detail;
  alert(typeof detail === "string" ? detail : "That did not work.");
  return false;
}
"""
_BOARDS: Final = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "lever_eu": "Lever (EU)",
    "ashby": "Ashby",
    "recruitee": "Recruitee",
    "personio": "Personio",
    "personio_com": "Personio",
    "smartrecruiters": "SmartRecruiters",
    "workday": "Workday",
}


def _input(name: str, label: str, value: str | int | None, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{escape(v, quote=True)}"' for k, v in attrs.items())
    shown = "" if value is None else escape(str(value), quote=True)
    return f'<label>{label}<br><input name="{name}" value="{shown}"{extra}></label>'


# How to turn on each title source, shown while it is off.
_SETUP: Final = {
    "InfoJobs": (
        "<strong>InfoJobs</strong> (Spain): set <code>APPLY_AGENT_INFOJOBS_CLIENT_ID</code> and "
        "<code>APPLY_AGENT_INFOJOBS_CLIENT_SECRET</code> from an app registered with your "
        'InfoJobs account at <a href="https://developer.infojobs.net" target="_blank" '
        'rel="noopener noreferrer">developer.infojobs.net</a>.'
    ),
}
_CREDITS: Final = {
    "InfoJobs": '<a href="https://www.infojobs.net" target="_blank" '
    'rel="noopener noreferrer">Offers from InfoJobs</a>.',
}


def _sources_note(sources: Sequence[TitleSource]) -> str:
    on = ", ".join(f"{escape(s.name)} ({escape(s.coverage)})" for s in sources)
    missing = [html for name, html in _SETUP.items() if name not in {s.name for s in sources}]
    parts = [f'<p class="muted">Searching {on}.</p>' if on else ""]
    if missing:
        lead = "To search by title, turn on" if not on else "Also available"
        items = "".join(f"<li>{item}</li>" for item in missing)
        parts.append(
            f'<div class="{"quiet" if not on else "muted"}">{lead}, then restart:'
            f"<ul>{items}</ul></div>"
        )
    if not on:
        parts.append('<p class="muted">Watching companies needs nothing.</p>')
    return "".join(parts)


def linkedin_location(where: str | None, country: str) -> str:
    """Where LinkedIn should search: 'Madrid, Spain', or just the country."""
    return f"{where}, {COUNTRY_NAMES[country]}" if where else COUNTRY_NAMES[country]


def _form(search: JobSearch | None, sources: Sequence[TitleSource]) -> str:
    what = search.what if search else None
    country = search.country if search else "es"
    where = search.where if search else None
    linkedin = escape(linkedin_jobs_url(what or "", linkedin_location(where, country)), quote=True)
    options = "".join(
        f'<option value="{code}"{" selected" if code == country else ""}'
        f' data-geo="{LINKEDIN_GEO_IDS.get(name, "")}">{name}</option>'
        for code, name in sorted(COUNTRY_NAMES.items(), key=lambda item: item[1])
    )
    disabled = "" if sources else " disabled"
    note = _sources_note(sources)
    return f"""
<section id="find">
<h2>Search by title</h2>
{note}
<form method="get" action="search" class="search-form" oninput="linkedInLink(this)">
  <fieldset{disabled}>
  {_input("what", "Title or keywords", what, required="required", placeholder="AI engineer")}
  {_input("where", "Place", search.where if search else None, placeholder="Madrid")}
  <label>Country<br><select name="country">{options}</select></label>
  {_input("company", "Company", search.company if search else None, placeholder="optional")}
  {
        _input(
            "max_days_old",
            "Posted in the last (days)",
            search.max_days_old if search else 14,
            type="number",
            min="1",
            max="60",
        )
    }
  <button type="submit">Search</button>
  </fieldset>
  <a id="linkedin" href="{linkedin}" target="_blank"
     rel="noopener noreferrer">Search on LinkedIn</a>
</form>
</section>"""


def _result_row(view: OfferView) -> str:
    offer = view.stored.offer
    link = escape(offer_link(view), quote=True)
    posted = offer.posted_on.isoformat() if offer.posted_on else "-"
    search = escape(f"{offer.company} {offer.title} {offer.location or ''}", quote=True)
    return (
        f'<tr data-search="{search}" data-score="{view.fit.score}" data-posted="{posted}"'
        f' data-level="{view.fit.level.value}">'
        f"<td>{fit_badge(view.fit)}</td>"
        f'<td><a href="{link}" target="_blank" rel="noopener noreferrer">'
        f"{escape(offer.title)}</a></td>"
        f"<td>{company_link(offer.company)}</td>"
        f"<td>{escape(offer.location or '-')}</td>"
        f'<td class="date">{posted}</td>'
        f"<td>{match_cell(view.match)}</td>"
        "</tr>"
    )


def _results(
    search: JobSearch,
    views: Sequence[OfferView],
    new: int,
    errors: Sequence[str],
    searched: Sequence[str],
) -> str:
    saved = escape(search.model_dump_json(), quote=True)
    save = (
        f'<button type="button" data-search="{saved}" onclick="saveSearch(this)">'
        "Save this search (it runs on every sync)</button>"
    )
    # A source that failed is reported; the others' results still show.
    failed = "".join(f'<p class="quiet">{escape(error)}</p>' for error in errors)
    if views:
        rows = "\n".join(_result_row(v) for v in sorted(views, key=rank))
        table = (
            '<div class="actions sort-controls"><label>Order by '
            '<select onchange="sortResults(this.value)">'
            '<option value="fit">Best fit</option><option value="posted">Newest</option>'
            "</select></label> "
            '<label><input type="checkbox" onchange="hidePoor(this.checked)"> '
            "Hide poor fits</label></div>"
            '<div class="wrap"><table id="result-table"><thead><tr><th>Fit</th><th>Role</th>'
            "<th>Company</th>"
            "<th>Location</th><th>Posted</th><th>Your applications</th></tr></thead>"
            f"<tbody>\n{rows}\n</tbody></table></div>"
        )
    elif searched and len(errors) < len(searched):
        table = '<p class="muted">Nothing found. Try fewer words or more days.</p>'
    else:
        table = ""
    count = len(views)
    on = ", ".join(escape(name) for name in searched)
    summary = f"{count} found on {on}, {new} new to your offers." if searched else ""
    credits = " ".join(_CREDITS[name] for name in searched if name in _CREDITS)
    return f"""
<section id="results">
<h2>Results <span class="count">({count})</span></h2>
<div class="actions"><span class="muted">{summary}</span> {save if searched else ""}</div>
{failed}{table}
<p class="muted">Results are added to <a href="offers">Job offers</a>, where you can mark them.
{credits}</p>
</section>"""


def _saved(searches: Sequence[SavedSearch]) -> str:
    if not searches:
        return '<p class="muted">No saved searches. Run a search, then save it.</p>'
    items = []
    for saved in searches:
        s = saved.search
        parts = [s.what, s.where, COUNTRY_NAMES[s.country], s.company and f"at {s.company}"]
        label = escape(" · ".join(p for p in parts if p))
        items.append(
            f'<li>{label} <span class="muted">(last {s.max_days_old} days)</span> '
            f'<button type="button" '
            f"onclick=\"send('search/saved/{saved.id}', 'DELETE', null, this)\">"
            "Remove</button></li>"
        )
    return f"<ul>{''.join(items)}</ul>"


def _companies(companies: Sequence[WatchedCompany], default_keywords: Sequence[str]) -> str:
    rows = []
    for company in companies:
        keywords = ", ".join(company.keywords) or "your profile's target words"
        places = ", ".join(company.places) or "anywhere"
        if company.last_error:
            status = f'<span class="quiet">{escape(company.last_error)}</span>'
        elif company.last_checked_at:
            status = f'<span class="muted">checked {company.last_checked_at:%Y-%m-%d %H:%M}</span>'
        else:
            status = '<span class="muted">checked on the next sync</span>'
        rows.append(
            f"<tr><td>{company_link(company.name)}</td>"
            f"<td>{_BOARDS.get(company.board, escape(company.board))} "
            f'<span class="muted">{escape(company.slug)}</span></td>'
            f'<td>{escape(keywords)}<br><span class="muted">{escape(places)}</span></td>'
            f"<td>{status}</td>"
            f'<td><button type="button" onclick="send(\'search/companies/{company.id}\', '
            f"'DELETE', null, this)\">Stop watching</button></td></tr>"
        )
    table = (
        '<div class="wrap"><table><thead><tr><th>Company</th><th>Job board</th>'
        "<th>Titles and places kept</th><th>Status</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        if rows
        else '<p class="muted">No companies watched yet.</p>'
    )
    hint = escape(", ".join(default_keywords[:6]) or "every title", quote=True)
    return f"""
<section id="companies">
<h2>Companies you watch <span class="count">({len(companies)})</span></h2>
<p class="muted">Their own job boards (Greenhouse, Lever, Ashby) are read on every sync, with
full descriptions, so the years asked are read, and a posting that disappears is marked as no
longer available.</p>
{table}
<form class="search-form" onsubmit="watch(this); return false">
  {
        _input(
            "name",
            "Company name or careers page URL",
            None,
            required="required",
            placeholder="Mollie, or https://jobs.lever.co/acme",
        )
    }
  {_input("keywords", "Titles with any of (comma-separated)", None, placeholder=hint)}
  {_input("places", "Places (comma-separated)", None, placeholder="Netherlands, Madrid")}
  <button type="submit">Watch</button>
</form>
</section>"""


def render_search(
    *,
    search: JobSearch | None,
    results: Sequence[OfferView],
    new: int,
    errors: Sequence[str],
    searched: Sequence[str],
    sources: Sequence[TitleSource],
    saved: Sequence[SavedSearch],
    companies: Sequence[WatchedCompany],
    default_keywords: Sequence[str],
) -> str:
    results_html = _results(search, results, new, errors, searched) if search else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search jobs</title>
<style>{STYLE}
.search-form fieldset {{ border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap;
  gap: .6rem; align-items: end; }}
.search-form {{ display: flex; flex-wrap: wrap; gap: .6rem; align-items: end; margin-top: .6rem; }}
#paste-box {{ font: inherit; width: 100%; max-width: 46rem; padding: .5rem; }}
#paste-result button {{ margin-top: .6rem; }}
.search-form input, .search-form select {{ font: inherit; padding: .35rem .5rem; width: 13rem;
  max-width: 100%; }}
</style>
</head>
<body>
<header>
  <div>
    {nav("search")}
    <h1>Search jobs</h1>
    <div class="muted">Search by title, place, country and company, or watch companies'
    own job boards.</div>
  </div>
</header>
{_form(search, sources)}
{results_html}
<section id="paste"><h2>Paste a LinkedIn search</h2>
<p class="muted">Run the search on LinkedIn (the link above opens it), scroll so the list
loads, then press Ctrl+A and Ctrl+C on that page and Ctrl+V here. The app reads what you
copied and never contacts LinkedIn. You see what it read before anything is added.</p>
<textarea id="paste-box" rows="2" placeholder="Click here and press Ctrl+V"
  onpaste="pasted(event)" aria-label="Paste a LinkedIn search page"></textarea>
<div id="paste-result"></div>
</section>
<section id="research"><h2>Research a company</h2>
<p class="muted">Its recent news, how it is hiring, and whom to contact.</p>
<form method="get" action="company" class="search-form">
  <label>Company<br><input name="name" required placeholder="Mollie"></label>
  <label>What it does (optional)<br><input name="context" placeholder="payments"></label>
  <button type="submit">Research</button>
</form></section>
<section id="saved"><h2>Saved searches <span class="count">({len(saved)})</span></h2>
{_saved(saved)}</section>
{_companies(companies, default_keywords)}
<script>{_SCRIPT}</script>
</body>
</html>
"""

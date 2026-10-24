"""The company page: what a company is going through, its hiring, whom to contact (D64).

Everything shown comes from the web or from job postings, so it is escaped.
News links and LinkedIn searches open in a new tab; the app itself never
visits LinkedIn.
"""

import json
from collections.abc import Sequence
from html import escape
from typing import Final

from apply_agent.api.offers_page import OfferView, fit_badge, match_cell, offer_link, rank
from apply_agent.api.page import STYLE, nav
from apply_agent.offers.company import CompanyResearch

_SCRIPT: Final = """
async function summarize(button) {
  button.disabled = true; button.textContent = "Reading the news...";
  const out = document.getElementById("summary");
  const response = await fetch("company/summary", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: button.dataset.body,
  });
  const data = await response.json().catch(() => ({}));
  out.hidden = false;
  out.textContent = response.ok ? data.summary : (data.detail || "Could not summarize.");
  button.textContent = "Summarize again"; button.disabled = false;
}
"""


def _link(url: str, text: str) -> str:
    return (
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f"{escape(text)}</a>"
    )


def _form(name: str, context: str | None) -> str:
    return f"""
<form method="get" action="company" class="search-form">
  <label>Company<br><input name="name" value="{escape(name, quote=True)}" required></label>
  <label>What it does (tells it from namesakes)<br>
    <input name="context" value="{escape(context or "", quote=True)}" placeholder="payments">
  </label>
  <button type="submit">Research</button>
</form>"""


def _hiring(research: CompanyResearch, views: Sequence[OfferView]) -> str:
    trend = research.hiring
    if not research.offers:
        return (
            '<p class="muted">No offers from this company yet. Watch it on the '
            '<a href="search#companies">Search page</a> to read its job board.</p>'
        )
    places = ", ".join(f"{escape(place)} ({count})" for place, count in trend.places) or "-"
    rows = "".join(
        f"<tr><td>{fit_badge(v.fit)}</td><td>"
        f"{_link(offer_link(v), v.stored.offer.title)}</td>"
        f"<td>{escape(v.stored.offer.location or '-')}</td><td>{match_cell(v.match)}</td></tr>"
        for v in sorted(views, key=rank)[:8]
        if v.stored.closed_at is None
    )
    table = (
        '<div class="wrap"><table><thead><tr><th>Fit</th><th>Role</th><th>Location</th>'
        f"<th>Your applications</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if rows
        else ""
    )
    return (
        f"<p><strong>{trend.open_now}</strong> open &middot; "
        f"{trend.new_last_7_days} new this week &middot; {trend.new_last_30_days} in 30 days "
        f"&middot; {trend.closed_last_30_days} closed in 30 days</p>"
        f'<p class="muted">Where: {places}. Counted from the offers this app keeps: alerts, '
        "searches, and the postings of a watched company that pass its filters.</p>"
        f"{table}"
    )


def _news(research: CompanyResearch) -> str:
    errors = "".join(
        f'<p class="quiet">Could not read {escape(error)}.</p>' for error in research.news_errors
    )
    if not research.news:
        hint = "" if research.context else " Try adding what the company does."
        return f'{errors}<p class="muted">No recent headlines name it.{hint}</p>'
    items = "".join(
        (f'<li><span class="date">{item.published:%Y-%m-%d}</span> ' if item.published else "<li>")
        + f'{_link(item.url, item.title)} <span class="muted">{escape(item.source)}</span></li>'
        for item in research.news
    )
    hint = (
        ""
        if research.context
        else '<p class="muted">Headlines about namesakes? Add what the company does above.</p>'
    )
    return f"{errors}<ul>{items}</ul>{hint}"


def _contacts(research: CompanyResearch) -> str:
    items = "".join(
        f"<li>{escape(c.kind.capitalize())}: "
        + (
            f'<a href="mailto:{escape(c.value, quote=True)}">{escape(c.value)}</a>'
            if c.kind == "email"
            else _link(c.value, c.value)
        )
        + f' <span class="muted">({escape(c.where)})</span></li>'
        for c in research.contacts
    )
    published = (
        f"<ul>{items}</ul>"
        if items
        else '<p class="muted">The company publishes no contact in the offers seen so far.</p>'
    )
    people = "".join(
        f'<li>{_link(p.url, p.role)} <span class="muted">{escape(p.why)}</span></li>'
        for p in research.people
    )
    return f"""
<h3>Published by the company</h3>
{published}
<h3>People to look for</h3>
<p class="muted">Each opens a LinkedIn search with your own account. The app does not visit
LinkedIn or keep anyone's details: choose whom to write to yourself.</p>
<ul>{people}</ul>"""


def render_company(
    research: CompanyResearch, views: Sequence[OfferView], *, can_summarize: bool
) -> str:
    links = " &middot; ".join(_link(url, label) for label, url in research.links.items())
    body = escape(json.dumps({"name": research.name, "context": research.context}), quote=True)
    summary = (
        f'<button type="button" data-body="{body}" onclick="summarize(this)">'
        "Summarize the news</button>"
        '<pre id="summary" class="summary" hidden></pre>'
        if can_summarize and research.news
        else (
            '<p class="muted">Set APPLY_AGENT_LLM to ollama or anthropic to summarize the news.</p>'
            if not can_summarize
            else ""
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(research.name)}</title>
<style>{STYLE}
.search-form {{ display: flex; flex-wrap: wrap; gap: .6rem; align-items: end; margin: .6rem 0; }}
.search-form input {{ font: inherit; padding: .35rem .5rem; width: 16rem; max-width: 100%; }}
.summary {{ white-space: pre-wrap; font: inherit; border-left: 3px solid var(--line);
  padding: .4rem .8rem; }}
h3 {{ font-size: 1rem; margin: 1.2rem 0 .3rem; }}
</style>
</head>
<body>
<header>
  <div>
    {nav("search")}
    <h1>{escape(research.name)}</h1>
    <div class="muted">{links}</div>
  </div>
</header>
{_form(research.name, research.context)}
<section id="hiring"><h2>Hiring</h2>{_hiring(research, views)}</section>
<section id="news"><h2>News</h2>{summary}{_news(research)}</section>
<section id="contact"><h2>Whom to contact</h2>{_contacts(research)}</section>
<script>{_SCRIPT}</script>
</body>
</html>
"""

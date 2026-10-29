"""A LinkedIn search page as the browser puts it on the clipboard. All of it is invented."""

from datetime import date

from apply_agent.domain.offers import OfferSource
from apply_agent.offers.pasted import parse_pasted_search
from tests.agent.helpers import T0

# The shape of a signed-in results list: one <li> per card, the title twice
# (once hidden for screen readers), hints under it, and the open posting's
# details beside the list linking to the same posting again.
SIGNED_IN = """
<html><body>
<header><a href="/feed/">Home</a><span>Jobs</span></header>
<div class="results"><div>Machine learning engineer in United Kingdom</div><div>143 results</div>
<ul>
  <li data-occludable-job-id="4481089788">
    <div class="card">
      <a href="/jobs/view/4481089788/?eBP=X&amp;refId=abc&amp;trackingId=def">
        <span aria-hidden="true"><strong>Machine Learning Engineer</strong></span>
        <span class="visually-hidden">Machine Learning Engineer with verification</span>
      </a>
      <div><span>Brisa Systems</span></div>
      <ul><li><span>London, England, United Kingdom (Hybrid)</span></li></ul>
      <ul><li><span>Promoted</span></li><li><span>Easy Apply</span></li></ul>
      <time datetime="2026-10-27">2 days ago</time>
      <button>Dismiss</button>
    </div>
  </li>
  <li data-occludable-job-id="4481000001">
    <a href="https://www.linkedin.com/jobs/search-results/?currentJobId=4481000001&amp;keywords=ml">
      <span>Backend Engineer (Python)</span></a>
    <div>Arcwell</div>
    <div>Cambridge, England, United Kingdom</div>
    <div>Viewed</div>
    <div>Actively reviewing applicants</div>
    <div>1 week ago</div>
  </li>
  <li><a href="/jobs/view/4481000002/"><span>No company line</span></a></li>
</ul></div>
<section class="details">
  <a href="/jobs/view/4481089788/">Machine Learning Engineer</a>
  <div>Brisa Systems · London · 2 days ago · 31 applicants</div>
  <script>var tracking = "/jobs/view/9999999999/";</script>
</section>
</body></html>
"""


def test_cards_are_read_from_a_pasted_results_page() -> None:
    page = parse_pasted_search(SIGNED_IN, T0)
    assert [(o.title, o.company, o.location) for o in page.offers] == [
        ("Machine Learning Engineer", "Brisa Systems", "London, England, United Kingdom (Hybrid)"),
        ("Backend Engineer (Python)", "Arcwell", "Cambridge, England, United Kingdom"),
    ]
    first = page.offers[0]
    assert first.source is OfferSource.LINKEDIN_SEARCH
    assert first.linkedin_id == "4481089788"
    assert first.url == "https://www.linkedin.com/jobs/view/4481089788/"
    assert first.posted_on == date(2026, 10, 27)
    # A card without a company line is counted, not guessed; the details pane
    # repeats a posting already read and is not counted twice.
    assert page.unreadable == 1


def test_without_list_items_each_link_starts_a_card() -> None:
    html = (
        '<div><a href="/jobs/view/5000000001/">Data Engineer</a><p>Cobalt Labs</p>'
        "<p>Madrid, Community of Madrid, Spain</p><p>3 days ago</p></div>"
        '<div><a href="/jobs/view/5000000002/">AI Engineer</a><p>Dunmore</p></div>'
    )
    page = parse_pasted_search(html, T0)
    assert [(o.title, o.company, o.location) for o in page.offers] == [
        ("Data Engineer", "Cobalt Labs", "Madrid, Community of Madrid, Spain"),
        ("AI Engineer", "Dunmore", None),
    ]


def test_text_without_links_gives_nothing() -> None:
    page = parse_pasted_search("Machine Learning Engineer\nBrisa Systems\nLondon", T0)
    assert page.offers == ()
    assert page.unreadable == 0

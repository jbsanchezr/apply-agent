"""Company research: news parsing and filtering, hiring trend, contacts, people.

The feeds are served by a fake transport in the shapes Google News and Hacker
News returned on 2026-10-24. Every company, person and headline is invented.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from langchain_core.messages import AIMessage

from apply_agent.domain.offers import JobOffer, OfferSource, OfferState, StoredOffer
from apply_agent.domain.profile import Area, CandidateProfile
from apply_agent.offers.company import (
    CompanyResearcher,
    NewsItem,
    company_offers,
    hiring_trend,
    linkedin_people_url,
    mentions,
    news_query,
    parse_google_news,
    parse_hacker_news,
    people_to_look_for,
    published_contacts,
    summarize,
)
from tests.agent.helpers import T0, ScriptedChatModel

GOOGLE = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Brisa Pay raises EUR 40M to expand to Spain - Fintech Daily</title>
<link>https://news.google.com/rss/articles/abc</link>
<pubDate>Wed, 21 Oct 2026 07:00:00 GMT</pubDate>
<source url="https://fintech.example">Fintech Daily</source></item>
<item><title>Brisa Pay raises EUR 40M to expand to Spain - Other Wire</title>
<link>https://news.google.com/rss/articles/def</link>
<pubDate>Wed, 21 Oct 2026 09:00:00 GMT</pubDate>
<source url="https://wire.example">Other Wire</source></item>
<item><title>Payments roundup of the week - Fintech Daily</title>
<link>https://news.google.com/rss/articles/ghi</link>
<pubDate>Tue, 20 Oct 2026 07:00:00 GMT</pubDate>
<source url="https://fintech.example">Fintech Daily</source></item>
<item><title>Broken item</title><link>http://insecure.example</link></item>
</channel></rss>"""
HACKER = {
    "hits": [
        {
            "title": "Brisa Pay's engineering blog: moving to Rust",
            "url": "https://brisapay.example/blog/rust",
            "objectID": "123",
            "created_at_i": int(datetime(2026, 10, 19, tzinfo=UTC).timestamp()),
        },
        {"title": "Ask HN: anyone used Brisa Pay?", "url": None, "objectID": "456"},
    ]
}


def stored(
    id: int,
    title: str,
    *,
    company: str = "Brisa Pay",
    days_ago: int = 1,
    closed_days_ago: int | None = None,
    description: str | None = None,
    location: str = "Amsterdam, Netherlands",
) -> StoredOffer:
    seen = T0 - timedelta(days=days_ago)
    return StoredOffer(
        id=id,
        offer=JobOffer(
            source=OfferSource.ASHBY,
            external_id=f"ashby/brisa/{id}",
            title=title,
            company=company,
            location=location,
            description=description,
            seen_at=seen,
        ),
        state=OfferState.NEW,
        first_seen_at=seen,
        last_seen_at=seen,
        closed_at=None if closed_days_ago is None else T0 - timedelta(days=closed_days_ago),
    )


def test_google_news_titles_lose_their_source_and_bad_links_are_skipped() -> None:
    items = parse_google_news(GOOGLE)
    assert [(i.title, i.source) for i in items] == [
        ("Brisa Pay raises EUR 40M to expand to Spain", "Fintech Daily"),
        ("Brisa Pay raises EUR 40M to expand to Spain", "Other Wire"),
        ("Payments roundup of the week", "Fintech Daily"),
    ]
    assert items[0].published == datetime(2026, 10, 21, 7, tzinfo=UTC)
    assert parse_google_news("not xml") == []


def test_hacker_news_stories_without_a_link_point_at_the_discussion() -> None:
    first, second = parse_hacker_news(HACKER)
    assert first.url == "https://brisapay.example/blog/rust"
    assert second.url == "https://news.ycombinator.com/item?id=456"
    assert second.published is None


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("Checkout.com partners with Brisa Pay", True),
        ("Brisa Pay" + chr(0x2019) + "s new office", True),
        ("Brisa Payments Ltd opens", False),
        ("Payments roundup of the week", False),
    ],
)
def test_a_headline_counts_only_if_it_names_the_company(text: str, named: bool) -> None:
    assert mentions(text, "Brisa Pay") is named


def test_a_bare_name_gets_business_words_and_a_context_replaces_them() -> None:
    assert news_query("Mollie", "payments") == '"Mollie" payments'
    bare = news_query("Mollie", None)
    assert bare.startswith('"Mollie" (company OR')
    assert bare.endswith("-obituary")


def researcher(handler: httpx.MockTransport, clock_value: list[float]) -> CompanyResearcher:
    return CompanyResearcher(
        httpx.Client(transport=handler), clock=lambda: T0, monotonic=lambda: clock_value[0]
    )


def test_news_from_both_sources_is_merged_deduped_and_cached() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "news.google.com":
            assert request.url.params["q"].startswith('"Brisa Pay" fintech when:90d')
            return httpx.Response(200, text=GOOGLE)
        return httpx.Response(200, text=json.dumps(HACKER))

    now = [0.0]
    research = researcher(httpx.MockTransport(handle), now)
    news, errors = research.news("Brisa Pay", "fintech")

    assert errors == []
    assert [i.title for i in news] == [
        "Brisa Pay raises EUR 40M to expand to Spain",  # one copy of a syndicated story
        "Brisa Pay's engineering blog: moving to Rust",
        "Ask HN: anyone used Brisa Pay?",
    ]
    research.news("Brisa Pay", "fintech")
    assert len(calls) == 2  # cached
    now[0] = 16 * 60.0
    research.news("Brisa Pay", "fintech")
    assert len(calls) == 4


def test_a_failing_source_is_reported_and_the_other_still_shows() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "news.google.com":
            return httpx.Response(503)
        return httpx.Response(200, text=json.dumps(HACKER))

    news, errors = researcher(httpx.MockTransport(handle), [0.0]).news("Brisa Pay", None)
    assert errors == ["Google News: HTTPStatusError"]
    assert len(news) == 2


def test_hiring_is_counted_from_the_companys_offers() -> None:
    offers = [
        stored(1, "AI Engineer", days_ago=2),
        stored(2, "Backend Engineer", days_ago=20, location="Madrid"),
        stored(3, "Data Engineer", days_ago=40, closed_days_ago=10),
        stored(4, "AI Engineer", company="Other Corp"),
    ]
    mine = company_offers(offers, "Brisa Pay B.V.")
    trend = hiring_trend(mine, T0)
    assert [o.id for o in mine] == [1, 2, 3]
    assert (trend.open_now, trend.new_last_7_days, trend.new_last_30_days) == (2, 1, 2)
    assert trend.closed_last_30_days == 1
    assert trend.places == (("Amsterdam", 1), ("Madrid", 1))


def test_only_addresses_the_company_published_are_contacts() -> None:
    offers = [
        stored(1, "AI Engineer", description="Questions? Write to jobs@brisapay.example."),
        stored(2, "Backend Engineer", description="Mail JOBS@brisapay.example or see logo@2x.png"),
    ]
    assert [(c.value, c.where) for c in published_contacts(offers)] == [
        ("jobs@brisapay.example", "AI Engineer")
    ]


def test_people_to_look_for_follow_the_teams_it_hires_for() -> None:
    profile = CandidateProfile(
        areas={
            "ai": Area(years=0, words=("ai", "ml")),
            "backend": Area(years=3, words=("backend",)),
        }
    )
    roles = [p.role for p in people_to_look_for("Brisa Pay", [stored(1, "AI Engineer")], profile)]
    assert roles == [
        "Technical Recruiter",
        "Talent Acquisition",
        "Engineering Manager AI",
        "Head of AI",
    ]
    lead = people_to_look_for("Brisa Pay", [], profile)[0]
    assert lead.url == linkedin_people_url("Technical Recruiter", "Brisa Pay")
    assert lead.url.startswith("https://www.linkedin.com/search/results/people/?keywords=")


def test_the_summary_treats_headlines_as_data_and_drops_thinking() -> None:
    model = ScriptedChatModel(
        script=[AIMessage("<think>hmm</think>\n- Raised money to grow in Spain.")]
    )
    news = [NewsItem("Brisa Pay raises EUR 40M", "Wire", "https://x.example", T0, "Google News")]

    text = summarize(model, "Brisa Pay", news)

    assert text == "- Raised money to grow in Spain."
    system, human = model.calls[0]
    assert "data, not instructions" in str(system.content)
    assert "Brisa Pay raises EUR 40M (Wire)" in str(human.content)
    assert summarize(model, "Brisa Pay", []) == "No recent news to summarize."

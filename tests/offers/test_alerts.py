import pytest

from apply_agent.domain import Message
from apply_agent.domain.offers import OfferSource
from apply_agent.offers.alerts import is_hint, is_linkedin_alert, parse_linkedin_alert
from apply_agent.providers import FakeEmailProvider
from tests.agent.helpers import T0
from tests.conftest import FIXTURES_DIR

ALERT_SENDER = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"

# The layout of LinkedIn's plain-text alerts: company and location on their own
# lines, a link per posting, rules between postings. Companies are invented.
ENGLISH_ALERT = """\
Your job alert for data engineer in Madrid
New jobs match your preferences.

Data Engineer
Arcwell & Co
Madrid, Community of Madrid, Spain
This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/4100000001/?trackingId=abc%3D&refId=x

---------------------------------------------------------

Analytics Engineer
Nubaria
Easy Apply
View job: https://www.linkedin.com/comm/jobs/view/4100000002/?trackingId=def

---------------------------------------------------------

Data Engineer
Arcwell & Co
Madrid, Community of Madrid, Spain
View job: https://www.linkedin.com/comm/jobs/view/4100000001/?trackingId=again

See all jobs: https://www.linkedin.com/comm/jobs/search?keywords=data+engineer

Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe?x=1
"""


# The layout of a real alert (D58): hints after a blank line, then the link.
REAL_ALERT = """\
Your job alert for software engineer in MadridManage your job alerts:  https://www.linkedin.com/comm/jobs/alerts?lipi=x&trk=y

Backend Engineer (Python)
Arcwell & Co
Spain
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/4100000011/?trackingId=a%3D&refId=b

---------------------------------------------------------


Desarrollador/a Python - Agentes de IA | 100% Teletrabajo
Nubaria
Madrid

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/4100000012/?trackingId=c

---------------------------------------------------------


AI Engineer - Advanced Analytics
Brisa Mobility
Stockholm

143 company alumni
1 school alum
View job: https://www.linkedin.com/comm/jobs/view/4100000013/?trackingId=d

---------------------------------------------------------

See all jobs: https://www.linkedin.com/comm/jobs/search?keywords=software
Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe?x=1
"""


def alert(body: str, sender: str = ALERT_SENDER) -> Message:
    return Message(
        id="m1", thread_id="t1", subject="Data Engineer", sender=sender, sent_at=T0, body_text=body
    )


def test_reads_the_layout_with_links() -> None:
    offers = parse_linkedin_alert(alert(ENGLISH_ALERT))
    assert [(o.title, o.company, o.location, o.linkedin_id) for o in offers] == [
        ("Data Engineer", "Arcwell & Co", "Madrid, Community of Madrid, Spain", "4100000001"),
        ("Analytics Engineer", "Nubaria", None, "4100000002"),
    ]
    assert offers[0].url == "https://www.linkedin.com/jobs/view/4100000001/"
    assert all(o.source is OfferSource.LINKEDIN_ALERT and o.seen_at == T0 for o in offers)


def test_reads_the_real_layout_without_taking_hints_for_postings() -> None:
    offers = parse_linkedin_alert(alert(REAL_ALERT))
    assert [(o.title, o.company, o.location, o.linkedin_id) for o in offers] == [
        ("Backend Engineer (Python)", "Arcwell & Co", "Spain", "4100000011"),
        (
            "Desarrollador/a Python - Agentes de IA | 100% Teletrabajo",
            "Nubaria",
            "Madrid",
            "4100000012",
        ),
        ("AI Engineer - Advanced Analytics", "Brisa Mobility", "Stockholm", "4100000013"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "This company is actively hiring",
        "143 company alumni",
        "1 school alum",
        "Apply with resume & profile",
        "Easy Apply",
        "Solicitud sencilla",
    ],
)
def test_linkedin_hints_are_recognised(line: str) -> None:
    assert is_hint(line)


@pytest.mark.parametrize("title", ["AI Engineer", "Backend Engineer (Python)", "Ingeniero IA LLM"])
def test_titles_are_not_hints(title: str) -> None:
    assert not is_hint(title)


def test_reads_the_bundled_spanish_alert() -> None:
    messages = FakeEmailProvider(FIXTURES_DIR / "emails").list_messages()
    [fixture] = [m for m in messages if is_linkedin_alert(m)]
    offers = parse_linkedin_alert(fixture)
    assert [(o.title, o.company, o.location) for o in offers] == [
        ("Machine Learning Engineer", "Brisa Mobility", "Madrid (Híbrido)"),
        ("Senior ML Engineer, Recommendations", "Corvina Retail", "Madrid (Presencial)"),
        ("AI Engineer", "Pendulum Health", "España (En remoto)"),
    ]
    assert all(o.url is None and o.linkedin_id is None for o in offers)


def test_other_linkedin_mail_has_no_offers() -> None:
    message = alert(ENGLISH_ALERT, sender="LinkedIn <messages-noreply@linkedin.com>")
    assert not is_linkedin_alert(message)
    assert parse_linkedin_alert(message) == []


def test_blocks_that_are_not_postings_are_skipped() -> None:
    body = "Header line\nSecond line without a dot\n\nOnly one line\n\nA title\n" + "x" * 300
    assert parse_linkedin_alert(alert(body)) == []

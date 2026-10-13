"""The company of mail sent as <company>@<platform> (D56)."""

import pytest

from apply_agent.agent.tool_node import with_tenant_company
from tests.agent.helpers import assessment

WORKDAY = "nexosystem@myworkday.example"


@pytest.mark.parametrize("company", [None, "Workday", "MyWorkday", "myworkday.example"])
def test_the_platform_or_no_company_becomes_the_sender_name(company: str | None) -> None:
    fixed = with_tenant_company(assessment(company=company), WORKDAY)
    assert fixed["company"] == "Nexosystem"


def test_a_company_the_model_found_in_the_text_is_kept() -> None:
    args = assessment(company="Lumora Energy")
    assert with_tenant_company(args, "Lumora <lumoraenergy@myworkday.example>") == args


def test_an_application_to_the_platform_itself_keeps_its_name() -> None:
    fixed = with_tenant_company(assessment(company="Workday"), "workday@myworkday.example")
    assert fixed["company"] == "Workday"


def test_other_senders_are_left_alone() -> None:
    # A company's own domain: "Careers" is a mailbox, not a company.
    args = assessment(company="Workday")
    assert with_tenant_company(args, "Careers <careers@workday.example>") == args


def test_mail_that_is_not_about_an_application_gets_no_company() -> None:
    args = assessment(company=None, is_job=False)
    assert with_tenant_company(args, WORKDAY) == args

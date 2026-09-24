import pytest

from apply_agent.domain import company_key, role_key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Kestrel Robotics GmbH", "kestrel robotics"),
        ("Parallax Payments, Inc.", "PARALLAX PAYMENTS"),
        ("Lumen & Vale", "Lumen and Vale"),
        ("Quillón Data S.L.", "Quillon Data"),
        ("  Halcyon   Health ", "Halcyon Health"),
    ],
)
def test_equivalent_company_names_share_a_key(a: str, b: str) -> None:
    assert company_key(a) == company_key(b)


def test_distinct_companies_keep_distinct_keys() -> None:
    assert company_key("Orbitra") != company_key("Orbitra Labs")


def test_a_bare_legal_suffix_is_not_stripped_to_nothing() -> None:
    assert company_key("AG") == "ag"


def test_role_key_normalises_punctuation_and_case() -> None:
    assert role_key("Backend Engineer, Platform") == role_key("backend engineer - platform")


def test_unknown_role_has_empty_key() -> None:
    assert role_key(None) == ""

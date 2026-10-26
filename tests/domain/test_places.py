import pytest

from apply_agent.domain.places import country_of, places_of


@pytest.mark.parametrize(
    ("place", "country"),
    [
        ("Madrid, Spain", "Spain"),
        ("Tres Cantos", "Spain"),
        ("Community of Madrid, Spain", "Spain"),
        ("Zug", "Switzerland"),
        ("Zürich Metropolitan Area", "Switzerland"),
        ("Utrecht, Netherlands", "Netherlands"),
        ("The Randstad, Netherlands", "Netherlands"),
        ("Östergötland County, Sweden", "Sweden"),
        ("Cardiff, London or Remote (UK)", "United Kingdom"),
        ("New York, US, New York", "United States"),
        ("Mountain View, California", "United States"),
        ("Remote - Europe", "Europe"),
        ("Somewhere Unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_the_country_is_read_from_names_and_cities(place: str | None, country: str | None) -> None:
    assert country_of(place) == country


@pytest.mark.parametrize(
    ("group", "market"),
    [
        ("Países Bajos", "Netherlands"),
        ("Alemania", "Germany"),
        ("Londres", "United Kingdom"),
        ("Madrid y Barcelona", "Spain"),
        ("Suiza", "Switzerland"),
        ("Fintech", None),
    ],
)
def test_a_group_label_names_its_market(group: str, market: str | None) -> None:
    assert country_of(group) == market


def test_several_places_are_split() -> None:
    assert places_of("Amsterdam, Netherlands; London, United Kingdom; Remote - Europe") == [
        ("Amsterdam, Netherlands", "Netherlands"),
        ("London, United Kingdom", "United Kingdom"),
        ("Remote - Europe", "Europe"),
    ]
    assert places_of(None) == []

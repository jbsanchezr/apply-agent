"""Migrating a database created before D62 (its tables copied from a real one)."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect

from apply_agent.domain.offers import JobOffer, OfferSource, OfferState
from apply_agent.storage import init_db, make_engine, make_session_factory
from apply_agent.storage.migrations import migrate
from apply_agent.storage.offers import OfferRepository
from tests.agent.helpers import T0

# What 0.1.x created, as SQLite stores it.
OLD_OFFERS = """CREATE TABLE job_offers (
	id INTEGER NOT NULL,
	offer_key VARCHAR(64) NOT NULL,
	source VARCHAR(32) NOT NULL,
	linkedin_id VARCHAR(20),
	title VARCHAR(200) NOT NULL,
	company VARCHAR(200) NOT NULL,
	location VARCHAR(200),
	url VARCHAR(500),
	posted_on DATE,
	state VARCHAR(32) NOT NULL,
	first_seen_at DATETIME NOT NULL,
	last_seen_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (offer_key),
	CONSTRAINT offersource CHECK (source IN ('linkedin_alert', 'linkedin_search')),
	CONSTRAINT offerstate CHECK (state IN ('new', 'interested', 'dismissed'))
)"""
OLD_CLOSURES = """CREATE TABLE offer_closures (
	offer_id INTEGER NOT NULL,
	reported_at DATETIME NOT NULL,
	age_days INTEGER NOT NULL,
	PRIMARY KEY (offer_id),
	FOREIGN KEY(offer_id) REFERENCES job_offers (id) ON DELETE CASCADE
)"""


@pytest.fixture
def old_engine(tmp_path: Path) -> Engine:
    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(OLD_OFFERS)
        conn.exec_driver_sql(OLD_CLOSURES)
        conn.exec_driver_sql(
            "INSERT INTO job_offers VALUES (7, 'linkedin:4100000001', 'linkedin_alert',"
            " '4100000001', 'Data Engineer', 'Arcwell', 'Madrid', NULL, NULL, 'interested',"
            " '2026-10-01 09:00:00.000000', '2026-10-03 09:00:00.000000')"
        )
        conn.exec_driver_sql(
            "INSERT INTO offer_closures VALUES (7, '2026-10-10 09:00:00.000000', 7)"
        )
    return engine


def table_sql(engine: Engine) -> str:
    with engine.connect() as conn:
        sql = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'job_offers'"
        ).scalar()
    assert isinstance(sql, str)
    return sql


def board_offer() -> JobOffer:
    return JobOffer(
        source=OfferSource.GREENHOUSE,
        external_id="arcwell/123",
        title="AI Engineer",
        company="Arcwell",
        description="Three years of Python.",
        seen_at=T0,
    )


def test_an_old_database_takes_new_sources_and_keeps_its_rows(old_engine: Engine) -> None:
    init_db(old_engine)
    offers = OfferRepository(make_session_factory(old_engine))

    assert offers.save([board_offer()]) == 1
    by_title = {o.offer.title: o for o in offers.list_offers()}
    old = by_title["Data Engineer"]
    assert old.id == 7
    assert old.state is OfferState.INTERESTED
    assert old.closed_at is not None
    assert offers.closing_ages() == [7]
    assert by_title["AI Engineer"].offer.description == "Three years of Python."


def test_closures_keep_their_foreign_key_after_the_rebuild(old_engine: Engine) -> None:
    init_db(old_engine)
    foreign = inspect(old_engine).get_foreign_keys("offer_closures")
    assert foreign[0]["referred_table"] == "job_offers"
    assert "detected" in {c["name"] for c in inspect(old_engine).get_columns("offer_closures")}
    offers = OfferRepository(make_session_factory(old_engine))
    assert offers.delete_offers([7]) == 1
    assert offers.closing_ages() == []  # the cascade still works


def test_migrating_twice_changes_nothing(old_engine: Engine) -> None:
    init_db(old_engine)
    before = table_sql(old_engine)
    migrate(old_engine)
    assert table_sql(old_engine) == before
    assert "'adzuna'" in before
    offers = OfferRepository(make_session_factory(old_engine))
    assert len(offers.list_offers()) == 1


def test_a_new_database_needs_no_rebuild(engine: Engine) -> None:
    offers = OfferRepository(make_session_factory(engine))
    offers.save([board_offer().model_copy(update={"seen_at": T0 + timedelta(days=1)})])
    migrate(engine)
    assert len(offers.list_offers()) == 1

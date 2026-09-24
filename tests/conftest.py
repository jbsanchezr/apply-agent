from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from apply_agent.storage import init_db, make_engine, make_session_factory

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    # A file-backed DB (not :memory:) so every connection sees the same data.
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as session:
        yield session

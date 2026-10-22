from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from apply_agent.storage import init_db, make_engine, make_session_factory

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def empty_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point "~" at an empty directory for every test.

    Default settings live under ~/.config/apply_agent (the Gmail token, the
    profile). On a developer's machine those files are real, so a test that
    reached them would pass there and fail in CI, and could act on real data.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


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

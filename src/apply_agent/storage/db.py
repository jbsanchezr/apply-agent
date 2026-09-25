"""Engine and session construction."""

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from apply_agent.storage.schema import Base


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    # SQLite ignores FOREIGN KEY constraints unless this is set per connection.
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL lets the API read while a background sync writes, and waiting a few
    # seconds for a lock beats failing a request immediately.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables. Safe to call repeatedly."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)

"""The few schema changes ``create_all`` cannot make on an existing database (D62).

``create_all`` adds missing tables but never changes one that exists (D9).
That was enough until D62 added offer sources: the source column carries a
CHECK constraint listing the allowed values, SQLite cannot alter a CHECK, and
a database created before D62 would refuse every new offer. So:

* ``job_offers`` is rebuilt when its CHECK lacks a source or the table lacks
  a column: create the new table, copy every row, drop the old one, rename.
  Foreign keys are off while it runs, so ``offer_closures`` keeps pointing at
  the same ids.
* ``offer_closures`` gains ``detected`` with ``ALTER TABLE ADD COLUMN``.

Each step checks first, so running it again changes nothing.
"""

import logging
from typing import Final

from sqlalchemy import Connection, Engine, Table, text
from sqlalchemy.schema import CreateTable

from apply_agent.domain.offers import OfferSource
from apply_agent.storage.schema import JobOfferRow

logger = logging.getLogger(__name__)

_NEW_TABLE: Final = "job_offers_new"


def _table_sql(conn: Connection, name: str) -> str | None:
    query = text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name")
    return conn.execute(query, {"name": name}).scalar()


def _columns(conn: Connection, name: str) -> list[str]:
    return [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({name})")]


def _offers_table_is_current(conn: Connection) -> bool:
    sql = _table_sql(conn, "job_offers")
    if sql is None:
        return True  # create_all will make it with the current schema
    table: Table = JobOfferRow.__table__  # type: ignore[assignment]
    has_sources = all(f"'{source.value}'" in sql for source in OfferSource)
    return has_sources and set(table.columns.keys()) <= set(_columns(conn, "job_offers"))


def _rebuild_offers(conn: Connection) -> int:
    table: Table = JobOfferRow.__table__  # type: ignore[assignment]
    create = str(CreateTable(table).compile(conn)).replace(
        "CREATE TABLE job_offers", f"CREATE TABLE {_NEW_TABLE}", 1
    )
    kept = [c for c in _columns(conn, "job_offers") if c in table.columns]
    names = ", ".join(kept)
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_NEW_TABLE}")
    conn.exec_driver_sql(create)
    # Only column names both the old table and the model know: never input.
    copy = f"INSERT INTO {_NEW_TABLE} ({names}) SELECT {names} FROM job_offers"  # noqa: S608
    copied = conn.exec_driver_sql(copy).rowcount
    conn.exec_driver_sql("DROP TABLE job_offers")
    conn.exec_driver_sql(f"ALTER TABLE {_NEW_TABLE} RENAME TO job_offers")
    return copied


def migrate(engine: Engine) -> None:
    """Bring an existing SQLite database up to the current schema. Idempotent."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        # Outside a transaction, or SQLite ignores it. Off, so dropping the old
        # offers table neither cascades to nor breaks offer_closures.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            with conn.begin():
                if not _offers_table_is_current(conn):
                    copied = _rebuild_offers(conn)
                    logger.info("job_offers rebuilt for new sources", extra={"rows": copied})
                closures = _columns(conn, "offer_closures")
                if closures and "detected" not in closures:
                    conn.exec_driver_sql(
                        "ALTER TABLE offer_closures ADD COLUMN detected BOOLEAN NOT NULL DEFAULT 0"
                    )
                broken = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"migration left broken foreign keys: {broken[:5]}")
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()

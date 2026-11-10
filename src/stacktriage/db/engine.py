"""Engine creation, pragmas and a minimal versioned migration runner.

A full migration framework (Alembic) would be heavier than this single-file
SQLite app needs. Schema versions are tracked with `PRAGMA user_version`;
each entry in MIGRATIONS runs once, in order, inside a transaction.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.pool import StaticPool

from .models import Base


def _v1_initial(conn: Connection) -> None:
    Base.metadata.create_all(conn)


# Append new migrations; never edit a released one.
MIGRATIONS: list[Callable[[Connection], None]] = [_v1_initial]


def make_engine(path: str | Path) -> Engine:
    """SQLite engine: WAL, foreign keys, busy timeout. ':memory:' uses one shared connection."""
    memory = str(path) == ":memory:"
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if memory:
        kwargs["poolclass"] = StaticPool
    engine = create_engine(f"sqlite:///{path}", **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: sqlite3.Connection, _rec) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        if not memory:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def migrate(engine: Engine) -> int:
    """Bring the schema to the latest version. Returns the resulting version."""
    with engine.begin() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar_one()
        if version > len(MIGRATIONS):
            raise RuntimeError(
                f"database schema v{version} is newer than this app supports (v{len(MIGRATIONS)})"
            )
        for i in range(version, len(MIGRATIONS)):
            MIGRATIONS[i](conn)
            conn.execute(text(f"PRAGMA user_version = {i + 1}"))
    return len(MIGRATIONS)

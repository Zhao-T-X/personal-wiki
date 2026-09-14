"""Persistence layer base (see docs/adr/ADR-004 Domain/Repository separation).

A Repository only reads and writes rows. Business rules do **not** live here.

Unit-of-work: when constructed with an existing ``conn`` the repository joins the
caller's transaction and never commits or closes it; without one it owns its own
connection (committing on write, rolling back on error).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import sqlite3

from ..cache import bump_data_epoch
from ..db import connect


class Repository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        if self._conn is not None:
            yield self._conn
            return
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        # Every write may invalidate something derived from the database, so the
        # data epoch is bumped on the way out of every write — including a
        # caller-owned transaction, whose commit happens later. Over-invalidating
        # costs a cache miss; under-invalidating would serve superseded knowledge.
        try:
            if self._conn is not None:
                # The caller owns the transaction: never commit or close here.
                yield self._conn
                return
            conn = connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            bump_data_epoch()


def rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def row(cursor) -> dict | None:
    r = cursor.fetchone()
    return dict(r) if r else None


def one(cursor, key: str):
    r = cursor.fetchone()
    return r[key] if r else None

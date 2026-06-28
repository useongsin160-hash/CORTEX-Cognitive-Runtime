import sqlite3
from typing import Any, Sequence

import aiosqlite

from app.core.errors import DatabaseError

_SQLALCHEMY_PREFIXES = ("sqlite+aiosqlite:///", "sqlite:///")


def _normalize_path(database_url: str) -> str:
    for prefix in _SQLALCHEMY_PREFIXES:
        if database_url.startswith(prefix):
            return database_url[len(prefix):]
    return database_url


class SQLiteRepository:
    def __init__(self, database_url: str) -> None:
        self._path = _normalize_path(database_url)

    async def execute(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> int:
        try:
            async with aiosqlite.connect(self._path) as conn:
                cursor = await conn.execute(sql, params or ())
                await conn.commit()
                return cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite execute failed: {exc}") from exc

    async def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(sql, params or ()) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite fetch_one failed: {exc}") from exc

    async def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            async with aiosqlite.connect(self._path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(sql, params or ()) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite fetch_all failed: {exc}") from exc

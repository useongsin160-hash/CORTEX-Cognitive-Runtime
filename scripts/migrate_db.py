"""Idempotent SQLite schema bootstrap for CORTEX-AEV.

Creates the `trace_logs` table used by Spinal Logger persistence. Safe to
re-run: every statement uses CREATE TABLE IF NOT EXISTS.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from app.core.config import get_settings
from app.db.sqlite import _normalize_path

TRACE_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS trace_logs (
    trace_id    TEXT NOT NULL,
    module_name TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}'
)
"""

TRACE_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_trace_logs_trace_id ON trace_logs(trace_id)"


async def migrate(database_url: str) -> Path:
    path = Path(_normalize_path(database_url))
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        await conn.execute(TRACE_LOGS_DDL)
        await conn.execute(TRACE_INDEX_DDL)
        await conn.commit()
    return path


def main() -> None:
    settings = get_settings()
    path = asyncio.run(migrate(settings.database_url))
    print(f"[migrate_db] schema ready at {path}")


if __name__ == "__main__":
    main()

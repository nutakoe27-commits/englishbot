#!/usr/bin/env python3
"""Apply the two additive MyTracker migrations using backend DATABASE_URL.

Run inside the backend image with db/migrations mounted read-only. Credentials
come from Compose's existing environment; no values or URLs are logged.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATIONS = ("0038_mytracker_installs.sql", "0041_mytracker_events.sql")


def statements(sql: str):
    # These two versioned files contain no stored routines or DELIMITER blocks.
    # Strip comment-only lines; their SQL string literals have no semicolons.
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for statement in body.split(";"):
        if statement.strip():
            yield statement.strip()


async def apply(directory: Path, expected_database: str, expected_user: str) -> None:
    value = os.environ.get("DATABASE_URL", "")
    if value.startswith("mysql+aiomysql://"):
        value = "mysql+asyncmy://" + value[len("mysql+aiomysql://"):]
    try:
        url = make_url(value)
    except Exception:
        raise RuntimeError("DATABASE_URL is missing or invalid") from None
    if url.drivername != "mysql+asyncmy":
        raise RuntimeError("DATABASE_URL must use mysql+asyncmy")
    if url.database != expected_database or url.username != expected_user:
        raise RuntimeError("DATABASE_URL database/user do not match the expected deployment target")
    sources = [(name, (directory / name).read_text(encoding="utf-8")) for name in MIGRATIONS]
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            if await connection.scalar(text("SELECT DATABASE()")) != expected_database:
                raise RuntimeError("Connection selected the wrong database")
            exists = await connection.scalar(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='schema_version'"
            ))
            if not exists:
                raise RuntimeError("Existing application schema is required; schema_version is missing")
            for name, source in sources:
                version = int(name.split("_", 1)[0])
                applied = await connection.scalar(text(
                    "SELECT COUNT(*) FROM schema_version WHERE version=:version"
                ), {"version": version})
                if applied:
                    print(f"Already applied {name}", flush=True)
                    continue
                for statement in statements(source):
                    await connection.exec_driver_sql(statement)
                print(f"Applied {name}", flush=True)
            for table in ("mytracker_installs", "mytracker_events"):
                exists = await connection.scalar(text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=:table"
                ), {"table": table})
                if not exists:
                    raise RuntimeError(f"Migration verification failed: {table} is missing")
            versions = await connection.scalar(text("SELECT COUNT(*) FROM schema_version WHERE version IN (38,41)"))
            if versions != 2:
                raise RuntimeError("Migration version verification failed")
        print("MyTracker tables and versions 38/41 verified", flush=True)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-user", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(apply(args.directory, args.expected_database, args.expected_user))
    except (RuntimeError, OSError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Driver errors can include a connection URL. Print only the type.
        print(f"Migration failed: {type(exc).__name__}; check DATABASE_URL, connectivity and CREATE/INSERT permissions", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

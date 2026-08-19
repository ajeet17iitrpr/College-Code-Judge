#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise SystemExit("This migration supports the SQLite deployment only.")
    raw = url[len(prefix):]
    return Path(raw).expanduser().resolve()


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def main() -> int:
    url = os.environ.get("DATABASE_URL", "sqlite:////home/ajeet/DLD/backend/data/judge.db")
    db_path = sqlite_path_from_url(url)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    print(f"Migrating: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")

        if not has_column(conn, "problems", "accepting_submissions"):
            conn.execute(
                "ALTER TABLE problems ADD COLUMN accepting_submissions BOOLEAN NOT NULL DEFAULT 1"
            )
            print("Added problems.accepting_submissions (existing problems default OPEN)")
        else:
            print("problems.accepting_submissions already exists")

        if not has_column(conn, "problems", "is_practice"):
            conn.execute(
                "ALTER TABLE problems ADD COLUMN is_practice BOOLEAN NOT NULL DEFAULT 0"
            )
            print("Added problems.is_practice (existing problems default NORMAL)")
        else:
            print("problems.is_practice already exists")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_problems_accepting_submissions "
            "ON problems(accepting_submissions)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_problems_is_practice ON problems(is_practice)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("R9 problem-control migration completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

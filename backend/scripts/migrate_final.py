from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.database import Base, engine


def sqlite_path() -> Path | None:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        return None
    return Path(settings.database_url[len(prefix):]).resolve()


def add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
        print(f"Added {table}.{column}")


def main() -> None:
    path = sqlite_path()
    if path and path.exists():
        backup_dir = PROJECT / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"judge_before_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(path, backup)
        print(f"Backup: {backup}")
        with sqlite3.connect(path) as connection:
            # Columns required when upgrading from the earlier staged project.
            for column, definition in {
                "full_name": "TEXT NOT NULL DEFAULT ''",
                "email": "TEXT NOT NULL DEFAULT ''",
                "enrollment_number": "TEXT NOT NULL DEFAULT ''",
                "status": "TEXT NOT NULL DEFAULT 'APPROVED'",
                "must_change_password": "INTEGER NOT NULL DEFAULT 0",
                "active_session_id": "INTEGER",
                "approved_at": "DATETIME",
                "approved_by_id": "INTEGER",
                "last_login_at": "DATETIME",
            }.items():
                add_column(connection, "users", column, definition)
            for column, definition in {
                "state": "TEXT NOT NULL DEFAULT 'ACTIVE'",
                "revoke_reason": "TEXT NOT NULL DEFAULT ''",
                "activated_at": "DATETIME",
                "last_seen_at": "DATETIME",
                "client_ip": "TEXT NOT NULL DEFAULT ''",
                "user_agent": "TEXT NOT NULL DEFAULT ''",
                "exam_generation_id": "TEXT",
                "exam_access_granted_at": "DATETIME",
            }.items():
                add_column(connection, "auth_sessions", column, definition)
            for column, definition in {
                "exam_mode_enabled": "INTEGER NOT NULL DEFAULT 1",
                "exam_duration_minutes": "INTEGER NOT NULL DEFAULT 60",
                "submission_limit": "INTEGER",
            }.items():
                add_column(connection, "problems", column, definition)
            for column, definition in {
                "exam_session_id": "TEXT",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "manual_score": "REAL",
                "manual_notes": "TEXT NOT NULL DEFAULT ''",
                "graded_by_id": "INTEGER",
                "graded_at": "DATETIME",
                "official": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                add_column(connection, "submissions", column, definition)
            for column, definition in {
                "exam_name": "TEXT NOT NULL DEFAULT 'Programming Examination'",
                "starts_at": "DATETIME",
                "ends_at": "DATETIME",
                "results_published": "INTEGER NOT NULL DEFAULT 0",
                "official_submission_policy": "TEXT NOT NULL DEFAULT 'BEST'",
                "passing_percentage": "REAL NOT NULL DEFAULT 40.0",
            }.items():
                add_column(connection, "exam_access_settings", column, definition)
            add_column(connection, "exam_access_attempts", "details", "TEXT NOT NULL DEFAULT ''")
            add_column(connection, "exam_flags", "client_ip", "TEXT NOT NULL DEFAULT ''")
            add_column(connection, "exam_flags", "user_agent", "TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE users SET full_name=username WHERE full_name IS NULL OR full_name='' ")
            connection.execute("UPDATE users SET enrollment_number=username WHERE enrollment_number IS NULL OR enrollment_number='' ")
            connection.execute("UPDATE users SET email='legacy_' || id || '@example.invalid' WHERE email IS NULL OR email='' ")
            connection.commit()
    Base.metadata.create_all(bind=engine)
    print("Final schema migration completed.")
    print("For a brand-new installation, use init_db.py directly.")


if __name__ == "__main__":
    main()

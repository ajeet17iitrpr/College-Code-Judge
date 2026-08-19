from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.access_control import get_access_setting
from app.config import settings
from app.database import Base, SessionLocal, engine


def sqlite_path() -> Path | None:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        return None
    return Path(settings.database_url[len(prefix):]).resolve()


def main() -> None:
    db_path = sqlite_path()
    if db_path and db_path.exists():
        backup = db_path.with_name(f"{db_path.name}.before_exam_features_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
        shutil.copy2(db_path, backup)
        print(f"Database backup: {backup}")

        with sqlite3.connect(db_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "exam_flags" in tables:
                columns = {row[1] for row in connection.execute('PRAGMA table_info("exam_flags")')}
                if "generation_id" not in columns:
                    connection.execute('ALTER TABLE "exam_flags" ADD COLUMN "generation_id" VARCHAR(36)')
                    print("Added exam_flags.generation_id")
                connection.execute('CREATE INDEX IF NOT EXISTS ix_exam_flags_generation_id ON exam_flags(generation_id)')
            if "access_ip_entries" in tables:
                columns = {row[1] for row in connection.execute('PRAGMA table_info("access_ip_entries")')}
                if "list_mode" not in columns:
                    connection.execute('ALTER TABLE "access_ip_entries" ADD COLUMN "list_mode" VARCHAR(16) NOT NULL DEFAULT "APPROVED"')
                    print("Added access_ip_entries.list_mode")
            connection.commit()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        get_access_setting(db)
        # Old forced-relogin grants are intentionally discarded. Current exam codes
        # are strictly one-use per student after this upgrade.
        try:
            db.execute(__import__('sqlalchemy').text('DELETE FROM exam_relogin_grants'))
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
    print("Exam/security feature migration completed.")


if __name__ == "__main__":
    main()

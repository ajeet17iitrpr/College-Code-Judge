from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import delete, inspect, select, text

from app.database import Base, SessionLocal, engine
from app.models import ExamFlag, ExamViolationSummary


def utcnow() -> datetime:
    return datetime.utcnow()


def add_column_if_missing(table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns(table)} if inspector.has_table(table) else set()
    if column not in columns:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        print(f"Added {table}.{column}")


def main() -> None:
    Base.metadata.create_all(bind=engine)
    add_column_if_missing("auth_sessions", "exam_ended_at", "exam_ended_at DATETIME")
    add_column_if_missing("auth_sessions", "exam_end_reason", "exam_end_reason VARCHAR(160) NOT NULL DEFAULT ''")
    add_column_if_missing("exam_violation_summaries", "event_counts_json", "event_counts_json TEXT NOT NULL DEFAULT '{}'")

    db = SessionLocal()
    try:
        existing = int(db.scalar(select(ExamViolationSummary.id).limit(1)) or 0)
        if not existing:
            grouped: dict[tuple[str, str, int], dict[str, object]] = defaultdict(
                lambda: {"count": 0, "first_at": None, "latest_at": None, "problem_id": None, "types": {}}
            )
            for flag in db.scalars(select(ExamFlag).order_by(ExamFlag.created_at.asc())):
                generation_id = flag.generation_id or "LEGACY"
                session_key = flag.session_id or "GLOBAL"
                key = (generation_id, session_key, flag.user_id)
                row = grouped[key]
                row["count"] = int(row["count"]) + 1
                if flag.problem_id is not None:
                    row["problem_id"] = flag.problem_id
                if row["first_at"] is None or flag.created_at < row["first_at"]:
                    row["first_at"] = flag.created_at
                if row["latest_at"] is None or flag.created_at > row["latest_at"]:
                    row["latest_at"] = flag.created_at
                types = row["types"]
                types[flag.event_type] = int(types.get(flag.event_type, 0)) + 1

            for (generation_id, session_key, user_id), row in grouped.items():
                db.add(
                    ExamViolationSummary(
                        generation_id=generation_id,
                        session_key=session_key,
                        user_id=user_id,
                        problem_id=row["problem_id"],
                        violation_count=int(row["count"]),
                        first_at=row["first_at"] or utcnow(),
                        latest_at=row["latest_at"] or utcnow(),
                        event_counts_json=json.dumps(row["types"], sort_keys=True),
                    )
                )
            db.flush()
            print(f"Backfilled {len(grouped)} permanent violation summary rows.")
        else:
            # Populate per-type counts where detailed records are still available.
            summaries = list(db.scalars(select(ExamViolationSummary)))
            for summary in summaries:
                try:
                    current = json.loads(summary.event_counts_json or "{}")
                except (TypeError, ValueError):
                    current = {}
                if current:
                    continue
                flags = list(
                    db.scalars(
                        select(ExamFlag).where(
                            ExamFlag.user_id == summary.user_id,
                            ExamFlag.generation_id == summary.generation_id,
                            ExamFlag.session_id == summary.session_key,
                        )
                    )
                )
                counts: dict[str, int] = {}
                for flag in flags:
                    counts[flag.event_type] = counts.get(flag.event_type, 0) + 1
                summary.event_counts_json = json.dumps(counts, sort_keys=True)
            print("Existing permanent violation summaries preserved.")

        cutoff = utcnow() - timedelta(hours=5)
        result = db.execute(delete(ExamFlag).where(ExamFlag.created_at < cutoff))
        db.commit()
        print(f"Removed {int(result.rowcount or 0)} detailed violation rows older than 5 hours.")
        print("Final V4 violation/session retention migration completed.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

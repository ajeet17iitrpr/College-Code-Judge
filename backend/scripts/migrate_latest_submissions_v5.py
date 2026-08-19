from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Submission


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        items = list(
            db.scalars(
                select(Submission).order_by(
                    Submission.user_id,
                    Submission.problem_id,
                    Submission.language,
                    Submission.created_at.desc(),
                    Submission.version.desc(),
                )
            )
        )
        grouped: dict[tuple[int, int, str], list[Submission]] = defaultdict(list)
        for item in items:
            grouped[(item.user_id, item.problem_id, item.language)].append(item)

        removed = 0
        kept = 0
        for group in grouped.values():
            if not group:
                continue
            kept += 1
            for old in group[1:]:
                db.delete(old)
                removed += 1
        db.commit()
        print(f"Latest-submission migration completed. Kept {kept} latest student/question/language records; removed {removed} older submissions.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

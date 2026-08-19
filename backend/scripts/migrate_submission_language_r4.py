#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select, text, update

from app.database import Base, SessionLocal, engine
from app.models import ExamFlag, RegistrationSetting, Submission, TestResult


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.get(RegistrationSetting, 1) is None:
            db.add(RegistrationSetting(id=1, domain_mode="ALL", custom_domain=""))
            db.commit()
        items = list(db.scalars(select(Submission).order_by(Submission.created_at.desc(), Submission.id.desc())))
        seen: set[tuple[int, int, str]] = set()
        duplicates: list[Submission] = []
        for item in items:
            key = (item.user_id, item.problem_id, item.language)
            if key in seen:
                duplicates.append(item)
            else:
                seen.add(key)

        for item in duplicates:
            db.execute(update(ExamFlag).where(ExamFlag.submission_id == item.id).values(submission_id=None))
            db.execute(delete(TestResult).where(TestResult.submission_id == item.id))
            db.delete(item)
        db.commit()

        remaining = list(db.scalars(select(Submission).order_by(Submission.user_id, Submission.problem_id, Submission.language)))
        # Stage globally unique temporary values so the legacy user/problem/version
        # uniqueness constraint cannot conflict while language slots are reassigned.
        for idx, item in enumerate(remaining, 1):
            item.version = 1_000_000 + idx
        db.commit()

        groups: dict[tuple[int, int], list[Submission]] = defaultdict(list)
        for item in remaining:
            groups[(item.user_id, item.problem_id)].append(item)
        preferred = {"c": 1, "cpp": 2, "python": 3}
        for group in groups.values():
            used: set[int] = set()
            next_slot = 4
            for item in sorted(group, key=lambda x: (preferred.get(x.language, 999), x.language)):
                slot = preferred.get(item.language)
                if slot is None or slot in used:
                    while next_slot in used:
                        next_slot += 1
                    slot = next_slot
                    next_slot += 1
                item.version = slot
                used.add(slot)
        db.commit()
    finally:
        db.close()

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_submission_user_problem_language "
            "ON submissions(user_id, problem_id, language)"
        ))

    print(f"R4 migration complete. Removed {len(duplicates)} older duplicate language submission(s).")
    print("Each student now has at most one stored solution per problem per language.")
    print("Registration-domain settings table is ready (default: ALL domains).")


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select
from app.database import SessionLocal
from app.models import Problem, TestCase, User


def main() -> None:
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.role == "admin"))
        if admin is None:
            raise SystemExit("Create an administrator first.")
        existing = db.scalar(select(Problem).where(Problem.title == "Add Two Numbers"))
        if existing:
            print(f"Sample problem already exists with ID {existing.id}.")
            return
        problem = Problem(
            title="Add Two Numbers",
            statement="Read two integers and print their sum.",
            input_format="Two space-separated integers a and b.",
            output_format="Print a + b.",
            constraints="-10^9 <= a, b <= 10^9",
            time_limit_ms=2000,
            memory_limit_mb=256,
            created_by=admin.id,
        )
        db.add(problem)
        db.flush()
        db.add_all(
            [
                TestCase(problem_id=problem.id, position=1, input_data="12 8\n", expected_output="20\n", is_public=True, points=20),
                TestCase(problem_id=problem.id, position=2, input_data="-4 11\n", expected_output="7\n", is_public=True, points=20),
                TestCase(problem_id=problem.id, position=3, input_data="999999999 1\n", expected_output="1000000000\n", is_public=False, points=30),
                TestCase(problem_id=problem.id, position=4, input_data="-100 -50\n", expected_output="-150\n", is_public=False, points=30),
            ]
        )
        db.commit()
        print(f"Created sample problem ID {problem.id}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

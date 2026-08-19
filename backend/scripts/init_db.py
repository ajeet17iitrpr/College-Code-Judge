from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import or_, select

from app.database import Base, SessionLocal, engine
from app.models import ExamAccessSetting, User
from app.security import hash_password, normalize_email, normalize_enrollment, validate_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize database and create/update the first administrator.")
    parser.add_argument("--name", default="System Administrator")
    parser.add_argument("--email", default="admin@college.local")
    parser.add_argument("--enrollment", default="ADMIN")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    validate_password(args.password)
    enrollment = normalize_enrollment(args.enrollment)
    email = normalize_email(args.email)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.get(ExamAccessSetting, 1) is None:
            db.add(ExamAccessSetting(id=1))
        user = db.scalar(select(User).where(or_(User.email == email, User.enrollment_number == enrollment)))
        if user is None:
            user = User(
                username=enrollment,
                full_name=args.name,
                email=email,
                enrollment_number=enrollment,
                password_hash=hash_password(args.password),
                role="admin",
                status="APPROVED",
                active=True,
            )
            db.add(user)
            print("Administrator created.")
        else:
            user.role = "admin"
            user.status = "APPROVED"
            user.active = True
            user.password_hash = hash_password(args.password)
            print("Administrator updated.")
        db.commit()
        print("Database is ready.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

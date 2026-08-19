from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import ExamAccessSetting, User
from app.security import hash_password, validate_password


PASSWORD = "CS203dld101"


def create_or_update(
    db,
    *,
    username: str,
    full_name: str,
    email: str,
    enrollment: str,
    role: str,
):
    user = db.scalar(
        select(User).where(User.username == username)
    )

    if user is None:
        user = User(
            username=username,
            full_name=full_name,
            email=email,
            enrollment_number=enrollment,
            password_hash=hash_password(PASSWORD),
            role=role,
            status="APPROVED",
            active=True,
        )
        db.add(user)
        print(f"[CREATE] {username}")
    else:
        user.full_name = full_name
        user.email = email
        user.enrollment_number = enrollment
        user.password_hash = hash_password(PASSWORD)
        user.role = role
        user.status = "APPROVED"
        user.active = True
        print(f"[UPDATE] {username}")

    # Handle fields added by later versions without depending on them.
    if hasattr(user, "must_change_password"):
        user.must_change_password = False

    if hasattr(user, "active_session_id"):
        user.active_session_id = None

    if hasattr(user, "approved_at") and user.approved_at is None:
        user.approved_at = datetime.now(timezone.utc)


def main():
    validate_password(PASSWORD)

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.get(ExamAccessSetting, 1) is None:
            db.add(ExamAccessSetting(id=1))

        for i in range(1, 10):
            create_or_update(
                db,
                username=f"user{i}",
                full_name=f"User {i}",
                email=f"user{i}@experiment.local",
                enrollment=f"USER{i}",
                role="student",
            )

        for i in range(1, 10):
            create_or_update(
                db,
                username=f"admin{i}",
                full_name=f"Admin {i}",
                email=f"admin{i}@experiment.local",
                enrollment=f"ADMIN{i}",
                role="admin",
            )

        db.commit()

        students = db.scalars(
            select(User).where(User.role == "student")
        ).all()

        admins = db.scalars(
            select(User).where(User.role == "admin")
        ).all()

        print()
        print(f"Students created: {len(students)}")
        print(f"Admins created:   {len(admins)}")
        print("Common password:  CS202dld101")
        print("Experiment accounts ready.")

    finally:
        db.close()


if __name__ == "__main__":
    main()

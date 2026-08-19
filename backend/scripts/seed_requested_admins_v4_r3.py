from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models import User
from app.security import hash_password, normalize_enrollment, utcnow, validate_password


REQUESTED_ADMINS = [
    ("admingurleen", "Gurleen", "Gurleen1234567"),
    ("adminajeet", "Ajeet", "Ajeet1234567"),
    ("adminravi", "Ravi", "Ravi1234567"),
    ("admingeeta", "Geeta", "Geeta1234567"),
    ("adminarif", "Arif", "Arif1234567"),
    ("adminanamika", "Anamika", "Anamika1234567"),
    ("adminapurva", "Apurva", "Apurva1234567"),
    ("adminajay", "Ajay", "Ajay1234567"),
    ("adminsonu", "Sonu", "Sonu1234567"),
]


def main() -> None:
    db = SessionLocal()
    created = 0
    skipped = 0
    try:
        for username, full_name, password in REQUESTED_ADMINS:
            validate_password(password)
            enrollment = normalize_enrollment(username)
            email = f"{username}@college.local"
            existing = db.scalar(
                select(User).where(
                    or_(
                        User.username == username,
                        User.enrollment_number == enrollment,
                        User.email == email,
                    )
                )
            )
            if existing is not None:
                if existing.role != "admin":
                    print(
                        f"[SKIP] {username}: matching non-admin account already exists; "
                        "not changing its role automatically."
                    )
                else:
                    print(f"[KEEP] {username}: administrator already exists; password left unchanged.")
                skipped += 1
                continue

            user = User(
                username=username,
                full_name=full_name,
                email=email,
                enrollment_number=enrollment,
                password_hash=hash_password(password),
                role="admin",
                status="APPROVED",
                active=True,
                approved_at=utcnow(),
                approved_by_id=None,
            )
            db.add(user)
            db.flush()
            print(f"[CREATE] {username}")
            created += 1

        db.commit()
        print(f"Done. Created {created} administrator(s); kept/skipped {skipped}.")
        print("Existing administrators and student users were not removed or modified.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from app.database import Base, engine
import app.models  # noqa: F401


def main() -> None:
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        if db_path.exists():
            backup = db_path.with_name(db_path.name + ".before_email_otp_v3_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak")
            shutil.copy2(db_path, backup)
            print(f"Database backup: {backup}")
    Base.metadata.create_all(bind=engine)
    print("Email OTP V3 migration completed.")
    print("Added email_otp_challenges table if it was missing.")


if __name__ == "__main__":
    main()

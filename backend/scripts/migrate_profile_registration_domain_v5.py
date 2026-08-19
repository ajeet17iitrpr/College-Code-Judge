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
            backup = db_path.with_name(
                db_path.name + ".before_profile_domain_v5_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
            )
            shutil.copy2(db_path, backup)
            print(f"Database backup: {backup}")
    Base.metadata.create_all(bind=engine)
    print("Profile + registration-domain V5 migration completed.")
    print("Added registration_email_settings table if it was missing.")
    print("Default registration email mode: ANY.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import RegistrationEmailSetting

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_registration_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    if domain.startswith("@"):
        domain = domain[1:]
    if not domain or "@" in domain or "://" in domain or "/" in domain or " " in domain:
        raise HTTPException(status_code=400, detail="Enter a valid email domain, for example example.edu.")
    if not DOMAIN_RE.fullmatch(domain):
        raise HTTPException(status_code=400, detail="Enter a valid email domain, for example example.edu.")
    return domain


def get_registration_email_setting(db: Session) -> RegistrationEmailSetting:
    setting = db.get(RegistrationEmailSetting, 1)
    if setting is None:
        setting = RegistrationEmailSetting(id=1, mode="ANY", custom_domain="")
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def allowed_registration_domain(setting: RegistrationEmailSetting) -> str | None:
    mode = (setting.mode or "ANY").upper()
    if mode == "GMAIL":
        return "gmail.com"
    if mode == "IITRPR":
        return "iitrpr.ac.in"
    if mode == "CUSTOM":
        return setting.custom_domain.strip().lower() or None
    return None


def registration_policy_payload(setting: RegistrationEmailSetting) -> dict:
    domain = allowed_registration_domain(setting)
    if domain:
        message = f"New registration is restricted to @{domain} email addresses."
    else:
        message = "New registration accepts any valid email domain."
    return {
        "mode": (setting.mode or "ANY").upper(),
        "custom_domain": setting.custom_domain or "",
        "allowed_domain": domain,
        "message": message,
    }


def enforce_registration_email(email: str, setting: RegistrationEmailSetting) -> None:
    domain = allowed_registration_domain(setting)
    if not domain:
        return
    actual = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if actual != domain:
        raise HTTPException(
            status_code=400,
            detail=f"Registration is restricted to @{domain} email addresses. Please use a different email with the allowed domain.",
        )

from __future__ import annotations

from ipaddress import ip_address, ip_network

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AccessControlSetting, AccessIPEntry

VALID_MODES = {"ALL", "APPROVED", "REJECTED"}


def get_access_setting(db: Session) -> AccessControlSetting:
    setting = db.get(AccessControlSetting, 1)
    if setting is None:
        setting = AccessControlSetting(id=1, mode="ALL")
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def normalize_ip_value(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("IP address is required.")
    if "/" in raw:
        return str(ip_network(raw, strict=False))
    return str(ip_address(raw))


def ip_value_matches(rule: str, candidate: str) -> bool:
    try:
        address = ip_address(candidate.strip())
        if "/" in rule:
            return address in ip_network(rule, strict=False)
        return address == ip_address(rule)
    except ValueError:
        return False


def is_client_allowed(db: Session, candidate_ip: str) -> bool:
    setting = get_access_setting(db)
    mode = (setting.mode or "ALL").upper()
    if mode == "ALL":
        return True
    entries = list(db.scalars(select(AccessIPEntry).where(AccessIPEntry.list_mode == mode).order_by(AccessIPEntry.id.asc())))
    matched = any(ip_value_matches(entry.ip_value, candidate_ip) for entry in entries)
    if mode == "APPROVED":
        return matched
    if mode == "REJECTED":
        return not matched
    return True

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..access_control import get_access_setting, is_client_allowed
from ..database import get_db
from ..services.rate_limit import client_ip

router = APIRouter(prefix="/api/access", tags=["access"])


@router.get("/check")
def check_access(request: Request, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    setting = get_access_setting(db)
    return {
        "allowed": is_client_allowed(db, ip),
        "mode": setting.mode,
        "client_ip": ip,
    }

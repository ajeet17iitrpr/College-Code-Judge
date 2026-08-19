from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuthSession, AuditLog, ExamAccessSetting, ExamFlag, User
from .services.rate_limit import client_ip

ENROLLMENT_RE = re.compile(r"^[A-Za-z0-9_.\-/]{2,64}$")
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


@dataclass
class AuthContext:
    user: User
    session: AuthSession


def utcnow() -> datetime:
    return datetime.utcnow()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password(password: str) -> None:
    if not 10 <= len(password) <= 128:
        raise HTTPException(status_code=400, detail="Password must contain 10-128 characters.")
    if not any(c.isupper() for c in password):
        raise HTTPException(status_code=400, detail="Password must include an upper-case letter.")
    if not any(c.islower() for c in password):
        raise HTTPException(status_code=400, detail="Password must include a lower-case letter.")
    if not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must include a number.")


def normalize_enrollment(value: str) -> str:
    normalized = value.strip().upper()
    if not ENROLLMENT_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid enrollment number format.")
    return normalized


def normalize_email(value: str) -> str:
    return value.strip().lower()


def get_exam_setting(db: Session) -> ExamAccessSetting:
    setting = db.get(ExamAccessSetting, 1)
    if setting is None:
        setting = ExamAccessSetting(id=1)
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def set_cookies(response: Response, raw_token: str, raw_csrf: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    common = {
        "max_age": max_age,
        "secure": settings.cookie_secure,
        "samesite": "strict",
        "path": "/",
        "domain": settings.cookie_domain,
    }
    response.set_cookie(settings.session_cookie_name, raw_token, httponly=True, **common)
    response.set_cookie(settings.csrf_cookie_name, raw_csrf, httponly=False, **common)


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/", domain=settings.cookie_domain)
    response.delete_cookie(settings.csrf_cookie_name, path="/", domain=settings.cookie_domain)


def create_session(
    db: Session,
    user: User,
    response: Response,
    request: Request,
    *,
    pending_exam: bool,
) -> AuthSession:
    raw_token = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    state = "PENDING_EXAM" if pending_exam else "ACTIVE"
    session = AuthSession(
        user_id=user.id,
        token_hash=digest(raw_token),
        csrf_hash=digest(raw_csrf),
        state=state,
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
        activated_at=None if pending_exam else utcnow(),
        client_ip=client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:512],
    )
    db.add(session)
    db.flush()
    if not pending_exam:
        if user.role == "admin":
            session.state = "ACTIVE"
            session.activated_at = utcnow()
        else:
            activate_session(db, user, session, reason="NEW_LOGIN")
    user.last_login_at = utcnow()
    db.commit()
    db.refresh(session)
    set_cookies(response, raw_token, raw_csrf)
    return session


def close_exam_access(session: AuthSession, reason: str) -> None:
    """Permanently close the timing record for an exam-authenticated browser session."""
    if session.exam_access_granted_at is not None and session.exam_ended_at is None:
        session.exam_ended_at = utcnow()
        session.exam_end_reason = reason[:160]


def activate_session(db: Session, user: User, session: AuthSession, reason: str = "SESSION_REPLACED") -> None:
    previous = list(
        db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.id != session.id,
                AuthSession.state.in_(["ACTIVE", "PENDING_EXAM"]),
            )
        )
    )
    for old in previous:
        close_exam_access(old, reason)
        old.state = "REVOKED"
        old.revoke_reason = reason
    session.state = "ACTIVE"
    session.activated_at = utcnow()
    session.last_seen_at = utcnow()
    user.active_session_id = session.id


def revoke_user_sessions(db: Session, user: User, reason: str) -> int:
    sessions = list(
        db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.state.in_(["ACTIVE", "PENDING_EXAM"]),
            )
        )
    )
    for session in sessions:
        close_exam_access(session, reason)
        session.state = "REVOKED"
        session.revoke_reason = reason
    user.active_session_id = None
    return len(sessions)


def record_audit(
    db: Session,
    request: Request,
    *,
    admin_id: int | None,
    action: str,
    target_user_id: int | None = None,
    details: str = "",
) -> None:
    db.add(
        AuditLog(
            admin_id=admin_id,
            target_user_id=target_user_id,
            action=action,
            details=details[:10_000],
            client_ip=client_ip(request),
        )
    )


def _load_session(request: Request, db: Session) -> AuthContext:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw_token)))
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid.")
    if session.expires_at <= utcnow():
        session.state = "REVOKED"
        session.revoke_reason = "SESSION_EXPIRED"
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")
    if session.state == "REVOKED":
        detail = "Your session was ended by the administrator." if "ADMIN" in session.revoke_reason else "This account was signed in elsewhere."
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    user = db.get(User, session.user_id)
    if user is None or not user.active or user.status != "APPROVED":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is not approved or active.")
    current_ip = client_ip(request)
    current_agent = request.headers.get("User-Agent", "")[:512]
    if user.role != "admin" and session.state == "ACTIVE":
        if session.client_ip and current_ip and session.client_ip != current_ip:
            db.add(ExamFlag(user_id=user.id, generation_id=session.exam_generation_id, event_type="IP_CHANGED", details=f"{session.client_ip} -> {current_ip}", client_ip=current_ip, user_agent=current_agent))
            session.client_ip = current_ip
        if session.user_agent and current_agent and session.user_agent != current_agent:
            db.add(ExamFlag(user_id=user.id, generation_id=session.exam_generation_id, event_type="BROWSER_CHANGED", details="User-Agent changed during the active session", client_ip=current_ip, user_agent=current_agent))
            session.user_agent = current_agent
    session.last_seen_at = utcnow()
    db.commit()
    return AuthContext(user=user, session=session)


def get_session_context(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    return _load_session(request, db)


def get_auth_context(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    auth = _load_session(request, db)
    if auth.session.state != "ACTIVE":
        raise HTTPException(status_code=428, detail="Exam code verification is required.")
    if auth.user.role != "admin" and auth.user.active_session_id != auth.session.id:
        auth.session.state = "REVOKED"
        auth.session.revoke_reason = "SESSION_REPLACED"
        db.commit()
        raise HTTPException(status_code=401, detail="This account was signed in elsewhere.")
    return auth


def _check_csrf(request: Request, auth: AuthContext) -> None:
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
    csrf_header = request.headers.get("X-CSRF-Token")
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    if not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    if not secrets.compare_digest(digest(csrf_header), auth.session.csrf_hash):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def require_session_csrf(request: Request, auth: AuthContext = Depends(get_session_context)) -> AuthContext:
    _check_csrf(request, auth)
    return auth


def require_csrf(request: Request, auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    _check_csrf(request, auth)
    return auth


def require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return auth


def require_admin_csrf(request: Request, auth: AuthContext = Depends(require_admin)) -> AuthContext:
    _check_csrf(request, auth)
    return auth

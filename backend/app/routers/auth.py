from __future__ import annotations

import json
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import EmailOTPChallenge, RecoveryRequest, RegistrationSetting, User
from ..schemas import (
    LoginRequest,
    OTPVerify,
    PasswordChange,
    PasswordResetRequest,
    PasswordResetVerify,
    RecoveryComplete,
    RecoveryCreate,
    RegistrationRequest,
)
from ..security import (
    AuthContext,
    clear_session_cookies,
    close_exam_access,
    create_session,
    digest,
    get_exam_setting,
    get_session_context,
    hash_password,
    normalize_email,
    normalize_enrollment,
    require_csrf,
    require_session_csrf,
    revoke_user_sessions,
    utcnow,
    validate_password,
    verify_password,
)
from ..services.mailer import MailDeliveryError, send_otp_email
from ..services.rate_limit import client_ip, client_key, rate_limiter

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def get_registration_setting(db: Session) -> RegistrationSetting:
    item = db.get(RegistrationSetting, 1)
    if item is None:
        item = RegistrationSetting(id=1, domain_mode="ALL", custom_domain="")
        db.add(item)
        db.flush()
    return item


def registration_domain_label(item: RegistrationSetting) -> str:
    mode = (item.domain_mode or "ALL").upper()
    if mode == "GMAIL":
        return "gmail.com"
    if mode == "IITRPR":
        return "iitrpr.ac.in"
    if mode == "CUSTOM":
        return item.custom_domain.strip().lower().lstrip("@")
    return "all domains"


def enforce_registration_domain(db: Session, email: str) -> None:
    item = get_registration_setting(db)
    mode = (item.domain_mode or "ALL").upper()
    if mode == "ALL":
        return
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    allowed = registration_domain_label(item)
    if not allowed or allowed == "all domains":
        return
    if domain != allowed:
        raise HTTPException(status_code=400, detail=f"Registration is currently limited to {allowed} email addresses.")


@router.get("/registration-policy")
def registration_policy(db: Session = Depends(get_db)) -> dict:
    item = get_registration_setting(db)
    return {"mode": item.domain_mode, "allowed_domain": registration_domain_label(item)}


def user_payload(auth: AuthContext) -> dict:
    pending = auth.session.state == "PENDING_EXAM"
    return {
        "id": auth.user.id,
        "username": auth.user.username,
        "full_name": auth.user.full_name,
        "email": auth.user.email,
        "enrollment_number": auth.user.enrollment_number,
        "role": auth.user.role,
        "status": auth.user.status,
        "must_change_password": auth.user.must_change_password,
        "exam_code_required": pending,
        "session_state": auth.session.state,
    }


def make_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def load_challenge(db: Session, challenge_id: str, purpose: str) -> EmailOTPChallenge:
    item = db.get(EmailOTPChallenge, challenge_id)
    if item is None or item.purpose != purpose or item.used_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or already used verification request.")
    if item.expires_at <= utcnow():
        raise HTTPException(status_code=400, detail="Verification code has expired. Request a new code.")
    if item.attempts >= settings.otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many incorrect OTP attempts. Request a new code.")
    return item


def verify_challenge_otp(db: Session, item: EmailOTPChallenge, otp: str) -> None:
    if not verify_password(item.otp_hash, otp):
        item.attempts += 1
        db.commit()
        raise HTTPException(status_code=400, detail="Incorrect verification code.")


@router.post("/register", status_code=201)
def register(payload: RegistrationRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Start email-verified registration. The account is created only after OTP verification."""
    rate_limiter.check(client_key(request, "register"), settings.login_rate_limit_per_minute)
    email = normalize_email(str(payload.email))
    enforce_registration_domain(db, email)
    enrollment = normalize_enrollment(payload.enrollment_number)
    validate_password(payload.password)
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    exists = db.scalar(
        select(User).where(or_(User.email == email, User.enrollment_number == enrollment, User.username == enrollment))
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="Email or enrollment number is already registered.")

    otp = make_otp()
    challenge = EmailOTPChallenge(
        id=str(uuid.uuid4()),
        purpose="REGISTRATION",
        email=email,
        user_id=None,
        otp_hash=hash_password(otp),
        payload_json=json.dumps(
            {
                "full_name": payload.full_name.strip(),
                "email": email,
                "enrollment_number": enrollment,
                "password_hash": hash_password(payload.password),
            }
        ),
        expires_at=utcnow() + timedelta(minutes=settings.otp_expiry_minutes),
        client_ip=client_ip(request),
    )
    db.add(challenge)
    db.flush()
    try:
        send_otp_email(to_email=email, otp=otp, purpose="REGISTRATION")
    except MailDeliveryError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Verification email could not be sent. Please try again.")
    db.commit()
    return {
        "status": "OTP_REQUIRED",
        "challenge_id": challenge.id,
        "expires_in_minutes": settings.otp_expiry_minutes,
        "message": "A verification OTP was sent to your email address.",
    }


@router.post("/register/verify")
def verify_registration(payload: OTPVerify, request: Request, db: Session = Depends(get_db)) -> dict:
    rate_limiter.check(client_key(request, "register-verify"), 20)
    item = load_challenge(db, payload.challenge_id, "REGISTRATION")
    verify_challenge_otp(db, item, payload.otp)
    try:
        data = json.loads(item.payload_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Registration request is invalid.") from exc

    email = normalize_email(data["email"])
    enforce_registration_domain(db, email)
    enrollment = normalize_enrollment(data["enrollment_number"])
    exists = db.scalar(
        select(User).where(or_(User.email == email, User.enrollment_number == enrollment, User.username == enrollment))
    )
    if exists is not None:
        item.used_at = utcnow()
        db.commit()
        raise HTTPException(status_code=409, detail="Email or enrollment number is already registered.")

    user = User(
        username=enrollment,
        full_name=data["full_name"],
        email=email,
        enrollment_number=enrollment,
        password_hash=data["password_hash"],
        role="student",
        status="APPROVED",
        active=True,
        approved_at=utcnow(),
        approved_by_id=None,
    )
    db.add(user)
    item.used_at = utcnow()
    db.commit()
    return {"status": "APPROVED", "message": "Email verified. Your account is ready to sign in."}


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rate_limiter.check(client_key(request, "login"), settings.login_rate_limit_per_minute)
    identifier = payload.identifier.strip()
    identifier_lower = identifier.lower()
    identifier_upper = identifier.upper()
    user = db.scalar(
        select(User).where(
            or_(
                User.username == identifier,
                User.email == identifier_lower,
                User.enrollment_number == identifier_upper,
            )
        )
    )
    if user is None or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=401, detail="Invalid login details.")
    if not user.active or user.status == "DISABLED":
        raise HTTPException(status_code=403, detail="Account is disabled.")
    if user.status != "APPROVED":
        raise HTTPException(status_code=403, detail="Account is not approved for login.")

    setting = get_exam_setting(db)
    pending_exam = user.role != "admin" and setting.enabled
    session = create_session(db, user, response, request, pending_exam=pending_exam)
    return user_payload(AuthContext(user=user, session=session))


@router.get("/me")
def me(auth: AuthContext = Depends(get_session_context)) -> dict:
    return user_payload(auth)


@router.post("/logout")
def logout(
    response: Response,
    auth: AuthContext = Depends(require_session_csrf),
    db: Session = Depends(get_db),
) -> dict:
    close_exam_access(auth.session, "LOGOUT")
    auth.session.state = "REVOKED"
    auth.session.revoke_reason = "LOGOUT"
    if auth.user.active_session_id == auth.session.id:
        auth.user.active_session_id = None
    db.commit()
    clear_session_cookies(response)
    return {"status": "logged_out"}


@router.post("/change-password")
def change_password(
    payload: PasswordChange,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(auth.user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    validate_password(payload.new_password)
    if payload.new_password != payload.confirm_new_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    auth.user.password_hash = hash_password(payload.new_password)
    auth.user.must_change_password = False
    revoke_user_sessions(db, auth.user, "PASSWORD_CHANGED")
    # Keep the current authenticated browser active.
    auth.session.state = "ACTIVE"
    auth.session.revoke_reason = ""
    auth.user.active_session_id = auth.session.id
    db.commit()
    return {"status": "password_changed"}


@router.post("/password-reset/request")
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rate_limiter.check(client_key(request, "password-reset"), 8)
    identifier = payload.identifier.strip()
    user = db.scalar(
        select(User).where(
            or_(
                User.email == identifier.lower(),
                User.enrollment_number == identifier.upper(),
                User.username == identifier.upper(),
            )
        )
    )

    # Return the same public response even for an unknown account to reduce account enumeration.
    fake_id = str(uuid.uuid4())
    if user is None or not user.active:
        return {
            "status": "OTP_REQUIRED",
            "challenge_id": fake_id,
            "expires_in_minutes": settings.otp_expiry_minutes,
            "message": "If the account exists, an OTP has been sent to its registered email address.",
        }

    otp = make_otp()
    challenge = EmailOTPChallenge(
        id=str(uuid.uuid4()),
        purpose="PASSWORD_RESET",
        email=user.email,
        user_id=user.id,
        otp_hash=hash_password(otp),
        payload_json="{}",
        expires_at=utcnow() + timedelta(minutes=settings.otp_expiry_minutes),
        client_ip=client_ip(request),
    )
    db.add(challenge)
    db.flush()
    try:
        send_otp_email(to_email=user.email, otp=otp, purpose="PASSWORD_RESET")
    except MailDeliveryError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Password-reset email could not be sent. Please try again.")
    db.commit()
    return {
        "status": "OTP_REQUIRED",
        "challenge_id": challenge.id,
        "expires_in_minutes": settings.otp_expiry_minutes,
        "message": "If the account exists, an OTP has been sent to its registered email address.",
    }


@router.post("/password-reset/verify")
def verify_password_reset(
    payload: PasswordResetVerify,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rate_limiter.check(client_key(request, "password-reset-verify"), 20)
    validate_password(payload.new_password)
    if payload.new_password != payload.confirm_new_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    item = load_challenge(db, payload.challenge_id, "PASSWORD_RESET")
    verify_challenge_otp(db, item, payload.otp)
    user = db.get(User, item.user_id) if item.user_id else None
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid password reset request.")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revoke_user_sessions(db, user, "PASSWORD_RESET")
    item.used_at = utcnow()
    db.commit()
    return {"status": "COMPLETED", "message": "Password reset successfully. Sign in with the new password."}


@router.post("/recovery/request", status_code=201)
def create_recovery_request(
    payload: RecoveryCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Account recovery still requires administrator review; normal forgot-password does not."""
    rate_limiter.check(client_key(request, "recovery"), 8)
    enrollment = payload.enrollment_number.strip().upper()
    email = payload.email.strip().lower()
    user = None
    if enrollment or email:
        user = db.scalar(select(User).where(or_(User.enrollment_number == enrollment, User.email == email)))
    request_id = str(uuid.uuid4())
    item = RecoveryRequest(
        id=request_id,
        request_type="ACCOUNT_RECOVERY",
        user_id=user.id if user else None,
        full_name=payload.full_name.strip(),
        enrollment_number=enrollment,
        email=email,
        new_email=payload.new_email.strip().lower(),
        reason=payload.reason.strip(),
        status="PENDING",
    )
    db.add(item)
    db.commit()
    return {
        "request_id": request_id,
        "status": "PENDING",
        "message": "Account recovery request submitted for administrator review.",
    }


@router.post("/recovery/complete")
def complete_recovery(payload: RecoveryComplete, db: Session = Depends(get_db)) -> dict:
    item = db.get(RecoveryRequest, payload.request_id)
    if item is None or item.request_type != "ACCOUNT_RECOVERY" or item.status != "APPROVED" or not item.reset_token_hash:
        raise HTTPException(status_code=400, detail="Account recovery request is not approved.")
    if item.token_expires_at is None or item.token_expires_at <= utcnow():
        raise HTTPException(status_code=400, detail="Recovery token has expired.")
    if not secrets.compare_digest(item.reset_token_hash, digest(payload.token)):
        raise HTTPException(status_code=400, detail="Invalid recovery token.")
    user = db.get(User, item.user_id) if item.user_id else None
    if user is None:
        raise HTTPException(status_code=404, detail="Associated account was not found.")
    validate_password(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.active = True
    user.status = "APPROVED"
    if item.new_email:
        conflict = db.scalar(select(User).where(User.email == item.new_email, User.id != user.id))
        if conflict:
            raise HTTPException(status_code=409, detail="Requested email is already used by another account.")
        user.email = item.new_email
    revoke_user_sessions(db, user, "ACCOUNT_RECOVERED")
    item.status = "COMPLETED"
    item.completed_at = utcnow()
    item.reset_token_hash = None
    db.commit()
    return {"status": "COMPLETED", "message": "Account recovered. Sign in using the new password."}

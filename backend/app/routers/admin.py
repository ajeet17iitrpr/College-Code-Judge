from __future__ import annotations

import difflib
import json
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    AccessControlSetting,
    AccessIPEntry,
    AuditLog,
    AuthSession,
    ExamAccessAttempt,
    ExamCodeUse,
    ExamFlag,
    ExamSession,
    ExamViolationSummary,
    Problem,
    RecoveryRequest,
    RegistrationSetting,
    Submission,
    TestCase,
    TestResult,
    User,
)
from ..schemas import (
    AccessIPCreate,
    AccessModeUpdate,
    AdminAccountCreate,
    AdminPasswordReset,
    AdminUserCreate,
    AuditRemark,
    ExamSettingUpdate,
    GenerateExamCodeRequest,
    ManualGrade,
    ProblemCreate,
    ProblemUpdate,
    RecoveryDecision,
    RegistrationDomainUpdate,
    TestCaseCreate,
    UserDecision,
    UserUpdate,
)
from ..access_control import get_access_setting, ip_value_matches, normalize_ip_value
from ..security import (
    AuthContext,
    close_exam_access,
    digest,
    get_exam_setting,
    hash_password,
    normalize_email,
    normalize_enrollment,
    record_audit,
    require_admin,
    require_admin_csrf,
    revoke_user_sessions,
    utcnow,
    validate_password,
)
from ..services.rate_limit import client_ip

router = APIRouter(prefix="/api/admin", tags=["admin"])


def user_row(user: User, db: Session) -> dict:
    submission_count = db.scalar(select(func.count(Submission.id)).where(Submission.user_id == user.id)) or 0
    flag_count = db.scalar(
        select(func.coalesce(func.sum(ExamViolationSummary.violation_count), 0)).where(ExamViolationSummary.user_id == user.id)
    ) or 0
    active_session = db.get(AuthSession, user.active_session_id) if user.active_session_id else None
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "enrollment_number": user.enrollment_number,
        "role": user.role,
        "status": user.status,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat() + "Z",
        "approved_at": user.approved_at.isoformat() + "Z" if user.approved_at else None,
        "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
        "submission_count": int(submission_count),
        "flag_count": int(flag_count),
        "online": bool(active_session and active_session.state == "ACTIVE"),
        "current_ip": active_session.client_ip if active_session else "",
        "last_activity": active_session.last_seen_at.isoformat() + "Z" if active_session else None,
    }


@router.get("/dashboard")
def dashboard(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return {
        "students": int(db.scalar(select(func.count(User.id)).where(User.role == "student")) or 0),
        "pending_users": int(db.scalar(select(func.count(User.id)).where(User.status == "PENDING")) or 0),
        "active_sessions": int(db.scalar(select(func.count(AuthSession.id)).where(AuthSession.state == "ACTIVE")) or 0),
        "pending_recovery": int(db.scalar(select(func.count(RecoveryRequest.id)).where(RecoveryRequest.status == "PENDING", RecoveryRequest.request_type == "ACCOUNT_RECOVERY")) or 0),
        "problems": int(db.scalar(select(func.count(Problem.id))) or 0),
        "submissions": int(db.scalar(select(func.count(Submission.id))) or 0),
        "queued": int(db.scalar(select(func.count(Submission.id)).where(Submission.status == "QUEUED")) or 0),
        "flags": int(db.scalar(select(func.coalesce(func.sum(ExamViolationSummary.violation_count), 0))) or 0),
    }


@router.get("/users")
def list_users(
    q: str = "",
    status: str = "",
    role: str = "",
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(User).order_by(User.role.asc(), User.full_name.asc())
    if status:
        query = query.where(User.status == status.upper())
    if role:
        normalized_role = role.strip().lower()
        if normalized_role not in {"student", "admin"}:
            raise HTTPException(status_code=400, detail="Invalid role filter.")
        query = query.where(User.role == normalized_role)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(User.full_name.ilike(pattern), User.email.ilike(pattern), User.enrollment_number.ilike(pattern))
        )
    return [user_row(user, db) for user in db.scalars(query)]


@router.post("/users", status_code=201)
def create_user(
    payload: AdminUserCreate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    enrollment = normalize_enrollment(payload.enrollment_number)
    email = normalize_email(str(payload.email))
    validate_password(payload.password)
    exists = db.scalar(select(User).where(or_(User.email == email, User.enrollment_number == enrollment)))
    if exists:
        raise HTTPException(status_code=409, detail="Email or enrollment number is already registered.")
    user = User(
        username=enrollment,
        full_name=payload.full_name.strip(),
        email=email,
        enrollment_number=enrollment,
        password_hash=hash_password(payload.password),
        role=payload.role,
        status="APPROVED" if payload.approved else "PENDING",
        approved_at=utcnow() if payload.approved else None,
        approved_by_id=auth.user.id if payload.approved else None,
    )
    db.add(user)
    db.flush()
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="USER_CREATED")
    db.commit()
    return user_row(user, db)


@router.get("/admins")
def list_admin_accounts(
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    admins = db.scalars(select(User).where(User.role == "admin").order_by(User.full_name.asc(), User.id.asc()))
    return [user_row(user, db) for user in admins]


@router.post("/admins", status_code=201)
def create_admin_account(
    payload: AdminAccountCreate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    validate_password(payload.password)
    username = payload.username.strip().lower()
    enrollment = normalize_enrollment(username)
    email = normalize_email(str(payload.email))
    exists = db.scalar(
        select(User).where(
            or_(User.username == username, User.email == email, User.enrollment_number == enrollment)
        )
    )
    if exists:
        raise HTTPException(status_code=409, detail="Username or email is already registered.")
    user = User(
        username=username,
        full_name=payload.full_name.strip(),
        email=email,
        enrollment_number=enrollment,
        password_hash=hash_password(payload.password),
        role="admin",
        status="APPROVED",
        active=True,
        approved_at=utcnow(),
        approved_by_id=auth.user.id,
    )
    db.add(user)
    db.flush()
    record_audit(
        db, request, admin_id=auth.user.id, target_user_id=user.id, action="ADMIN_ACCOUNT_CREATED", details=username
    )
    db.commit()
    return user_row(user, db)


@router.post("/admins/{user_id}/reset-password")
def reset_admin_password(
    user_id: int,
    payload: AdminPasswordReset,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=404, detail="Administrator not found.")
    if user.id == auth.user.id:
        raise HTTPException(status_code=400, detail="Use the authenticated change-password flow for your own account.")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    validate_password(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revoke_user_sessions(db, user, "ADMIN_PASSWORD_RESET")
    record_audit(
        db, request, admin_id=auth.user.id, target_user_id=user.id, action="ADMIN_PASSWORD_RESET"
    )
    db.commit()
    return {"status": "password_reset"}


@router.get("/users/{user_id}")
def user_detail(
    user_id: int,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    sessions = list(db.scalars(select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at.desc()).limit(100)))
    submissions = list(db.scalars(select(Submission).where(Submission.user_id == user_id).order_by(Submission.created_at.desc()).limit(500)))
    flags = list(db.scalars(select(ExamFlag).where(ExamFlag.user_id == user_id).order_by(ExamFlag.created_at.desc()).limit(1000)))
    code_uses = list(db.scalars(select(ExamCodeUse).where(ExamCodeUse.user_id == user_id).order_by(ExamCodeUse.used_at.desc())))
    return {
        "user": user_row(user, db),
        "sessions": [
            {
                "id": s.id,
                "state": s.state,
                "revoke_reason": s.revoke_reason,
                "client_ip": s.client_ip,
                "user_agent": s.user_agent,
                "created_at": s.created_at.isoformat() + "Z",
                "last_seen_at": s.last_seen_at.isoformat() + "Z",
            }
            for s in sessions
        ],
        "submissions": [
            {
                "id": s.id,
                "problem_id": s.problem_id,
                "problem_title": s.problem.title,
                    "language": s.language,
                "status": s.status,
                "verdict": s.verdict,
                "automatic_score": s.score,
                "manual_score": s.manual_score,
                "final_score": s.final_score,
                "official": s.official,
                "created_at": s.created_at.isoformat() + "Z",
            }
            for s in submissions
        ],
        "flags": [
            {
                "id": f.id,
                "problem_id": f.problem_id,
                "submission_id": f.submission_id,
                "event_type": f.event_type,
                "details": f.details,
                "client_ip": f.client_ip,
                "created_at": f.created_at.isoformat() + "Z",
            }
            for f in flags
        ],
        "exam_code_uses": [
            {
                "generation_id": item.generation_id,
                "used_at": item.used_at.isoformat() + "Z",
                "client_ip": item.client_ip,
            }
            for item in code_uses
        ],
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    values = payload.model_dump(exclude_unset=True)
    if "email" in values:
        values["email"] = normalize_email(str(values["email"]))
    if "enrollment_number" in values:
        values["enrollment_number"] = normalize_enrollment(values["enrollment_number"])
        values["username"] = values["enrollment_number"]
    for key, value in values.items():
        setattr(user, key, value)
    if user.status == "APPROVED" and user.approved_at is None:
        user.approved_at = utcnow()
        user.approved_by_id = auth.user.id
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="USER_UPDATED", details=str(values))
    db.commit()
    return user_row(user, db)


@router.post("/users/{user_id}/approve")
def approve_user(
    user_id: int,
    payload: UserDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.status = "APPROVED"
    user.active = True
    user.approved_at = utcnow()
    user.approved_by_id = auth.user.id
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="REGISTRATION_APPROVED", details=payload.remarks)
    db.commit()
    return {"status": "APPROVED"}


@router.post("/users/{user_id}/reject")
def reject_user(
    user_id: int,
    payload: UserDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.status = "REJECTED"
    revoke_user_sessions(db, user, "ADMIN_REJECTED")
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="REGISTRATION_REJECTED", details=payload.remarks)
    db.commit()
    return {"status": "REJECTED"}


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: int,
    payload: UserDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role == "admin" and user.id == auth.user.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own administrator account.")
    user.active = False
    revoke_user_sessions(db, user, "ADMIN_DISABLED")
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="USER_DISABLED", details=payload.remarks)
    db.commit()
    return {"status": "DISABLED", "active": False}


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: int,
    payload: UserDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.active = True
    if user.status == "DISABLED":
        user.status = "APPROVED"
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="USER_ENABLED", details=payload.remarks)
    db.commit()
    return {"status": "ENABLED", "active": True}


@router.get("/recovery")
def recovery_requests(
    status: str = "PENDING",
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(RecoveryRequest).where(RecoveryRequest.request_type == "ACCOUNT_RECOVERY").order_by(RecoveryRequest.created_at.desc())
    if status:
        query = query.where(RecoveryRequest.status == status.upper())
    return [
        {
            "id": item.id,
            "request_type": item.request_type,
            "user_id": item.user_id,
            "full_name": item.full_name,
            "enrollment_number": item.enrollment_number,
            "email": item.email,
            "new_email": item.new_email,
            "reason": item.reason,
            "status": item.status,
            "admin_remarks": item.admin_remarks,
            "created_at": item.created_at.isoformat() + "Z",
        }
        for item in db.scalars(query)
    ]


@router.post("/recovery/{request_id}/approve")
def approve_recovery(
    request_id: str,
    payload: RecoveryDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(RecoveryRequest, request_id)
    if item is None or item.request_type != "ACCOUNT_RECOVERY" or item.status != "PENDING":
        raise HTTPException(status_code=404, detail="Pending account recovery request not found.")
    user = db.get(User, item.user_id) if item.user_id else None
    if user is None:
        user = db.scalar(
            select(User).where(or_(User.enrollment_number == item.enrollment_number, User.email == item.email))
        )
    if user is None:
        raise HTTPException(status_code=404, detail="No matching student account was found.")
    token = secrets.token_urlsafe(12)
    item.user_id = user.id
    if payload.approved_email:
        item.new_email = normalize_email(payload.approved_email)
    item.status = "APPROVED"
    item.reset_token_hash = digest(token)
    item.token_expires_at = utcnow() + timedelta(minutes=payload.token_valid_minutes)
    item.reviewed_by_id = auth.user.id
    item.reviewed_at = utcnow()
    item.admin_remarks = payload.remarks
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="RECOVERY_APPROVED", details=item.request_type)
    db.commit()
    return {
        "status": "APPROVED",
        "request_id": item.id,
        "reset_token": token,
        "expires_at": item.token_expires_at.isoformat() + "Z",
        "message": "Show this one-time token to the student. It will not be displayed again.",
    }


@router.post("/recovery/{request_id}/reject")
def reject_recovery(
    request_id: str,
    payload: RecoveryDecision,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(RecoveryRequest, request_id)
    if item is None or item.request_type != "ACCOUNT_RECOVERY" or item.status != "PENDING":
        raise HTTPException(status_code=404, detail="Pending account recovery request not found.")
    item.status = "REJECTED"
    item.reviewed_by_id = auth.user.id
    item.reviewed_at = utcnow()
    item.admin_remarks = payload.remarks
    record_audit(db, request, admin_id=auth.user.id, target_user_id=item.user_id, action="RECOVERY_REJECTED")
    db.commit()
    return {"status": "REJECTED"}


@router.get("/exam-settings")
def exam_settings(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    setting = get_exam_setting(db)
    return {
        "enabled": setting.enabled,
        "exam_name": setting.exam_name,
        "generation_id": setting.active_generation_id,
        "generated_at": setting.generated_at.isoformat() + "Z" if setting.generated_at else None,
        "expires_at": setting.expires_at.isoformat() + "Z" if setting.expires_at else None,
        "starts_at": setting.starts_at.isoformat() + "Z" if setting.starts_at else None,
        "ends_at": setting.ends_at.isoformat() + "Z" if setting.ends_at else None,
        "results_published": setting.results_published,
        "official_submission_policy": setting.official_submission_policy,
        "passing_percentage": setting.passing_percentage,
    }


@router.patch("/exam-settings")
def update_exam_settings(
    payload: ExamSettingUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    setting = get_exam_setting(db)
    values = payload.model_dump(exclude_unset=True)
    was_enabled = setting.enabled
    for key, value in values.items():
        setattr(setting, key, value)
    if was_enabled and values.get("enabled") is False:
        active_exam_sessions = list(
            db.scalars(
                select(AuthSession).where(
                    AuthSession.exam_access_granted_at.is_not(None),
                    AuthSession.exam_ended_at.is_(None),
                )
            )
        )
        for session in active_exam_sessions:
            close_exam_access(session, "EXAM_MODE_OFF")
    setting.updated_at = utcnow()
    record_audit(db, request, admin_id=auth.user.id, action="EXAM_SETTINGS_UPDATED", details=str(values))
    db.commit()
    return exam_settings(auth, db)


@router.post("/exam-code/generate")
def generate_exam_code(
    payload: GenerateExamCodeRequest,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    setting = get_exam_setting(db)
    code = f"{secrets.randbelow(1_000_000):06d}"
    generation_id = str(uuid.uuid4())
    setting.active_generation_id = generation_id
    setting.code_hash = hash_password(code)
    setting.generated_at = utcnow()
    setting.expires_at = utcnow() + timedelta(minutes=payload.valid_minutes)
    setting.generated_by_id = auth.user.id
    setting.updated_at = utcnow()
    record_audit(db, request, admin_id=auth.user.id, action="EXAM_CODE_GENERATED", details=generation_id)
    db.commit()
    return {
        "code": code,
        "generation_id": generation_id,
        "expires_at": setting.expires_at.isoformat() + "Z",
        "message": "Announce this code in the lab. It is displayed only now.",
    }


@router.get("/exam-code/activity")
def exam_code_activity(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    uses = list(db.scalars(select(ExamCodeUse).order_by(ExamCodeUse.used_at.desc()).limit(1000)))
    attempts = list(db.scalars(select(ExamAccessAttempt).order_by(ExamAccessAttempt.created_at.desc()).limit(1000)))
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_({x.user_id for x in uses if x.user_id} | {x.user_id for x in attempts if x.user_id})))}
    return {
        "uses": [
            {
                "user_id": x.user_id,
                "student": users[x.user_id].full_name if x.user_id in users else "",
                "enrollment_number": users[x.user_id].enrollment_number if x.user_id in users else "",
                "generation_id": x.generation_id,
                "client_ip": x.client_ip,
                "used_at": x.used_at.isoformat() + "Z",
            }
            for x in uses
        ],
        "attempts": [
            {
                "user_id": x.user_id,
                "student": users[x.user_id].full_name if x.user_id in users else "",
                "event_type": x.event_type,
                "details": x.details,
                "client_ip": x.client_ip,
                "created_at": x.created_at.isoformat() + "Z",
            }
            for x in attempts
        ],
    }



@router.post("/users/{user_id}/force-relogin")
def force_relogin_user(
    user_id: int,
    payload: AuditRemark,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None or user.role != "student":
        raise HTTPException(status_code=404, detail="Student not found.")
    count = revoke_user_sessions(db, user, "ADMIN_FORCE_RELOGIN")
    record_audit(db, request, admin_id=auth.user.id, target_user_id=user.id, action="FORCE_RELOGIN_USER", details=payload.remarks)
    db.commit()
    return {"revoked_sessions": count}


@router.post("/force-relogin-all")
def force_relogin_all(
    payload: AuditRemark,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    students = list(db.scalars(select(User).where(User.role == "student", User.status == "APPROVED")))
    revoked = 0
    for user in students:
        revoked += revoke_user_sessions(db, user, "ADMIN_FORCE_RELOGIN_ALL")
    record_audit(db, request, admin_id=auth.user.id, action="FORCE_RELOGIN_ALL", details=payload.remarks)
    db.commit()
    return {"students": len(students), "revoked_sessions": revoked}


@router.get("/registration-settings")
def registration_settings(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    item = db.get(RegistrationSetting, 1)
    if item is None:
        item = RegistrationSetting(id=1, domain_mode="ALL", custom_domain="")
        db.add(item)
        db.commit()
    return {
        "mode": item.domain_mode,
        "custom_domain": item.custom_domain,
        "effective_domain": (
            "all domains" if item.domain_mode == "ALL" else
            "gmail.com" if item.domain_mode == "GMAIL" else
            "iitrpr.ac.in" if item.domain_mode == "IITRPR" else item.custom_domain
        ),
    }


@router.patch("/registration-settings")
def update_registration_settings(
    payload: RegistrationDomainUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(RegistrationSetting, 1)
    if item is None:
        item = RegistrationSetting(id=1, domain_mode="ALL", custom_domain="")
        db.add(item)
    mode = payload.mode.upper()
    custom = payload.custom_domain.strip().lower().lstrip("@")
    if mode == "CUSTOM":
        if not custom or "@" in custom or "/" in custom or " " in custom or "." not in custom:
            raise HTTPException(status_code=400, detail="Enter a valid custom email domain, for example example.edu.")
    item.domain_mode = mode
    item.custom_domain = custom if mode == "CUSTOM" else ""
    item.updated_at = utcnow()
    item.updated_by_id = auth.user.id
    record_audit(db, request, admin_id=auth.user.id, action="REGISTRATION_DOMAIN_UPDATED", details=f"{mode}:{item.custom_domain}")
    db.commit()
    return {"mode": item.domain_mode, "custom_domain": item.custom_domain}


@router.get("/ip-access")
def get_ip_access(
    request: Request,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    setting = get_access_setting(db)
    entries = list(db.scalars(select(AccessIPEntry).order_by(AccessIPEntry.created_at.desc())))
    return {
        "mode": setting.mode,
        "current_ip": client_ip(request),
        "entries": [
            {
                "id": item.id,
                "list_mode": item.list_mode,
                "ip_value": item.ip_value,
                "note": item.note,
                "created_at": item.created_at.isoformat() + "Z",
            }
            for item in entries
        ],
    }


@router.patch("/ip-access")
def update_ip_access(
    payload: AccessModeUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    setting = get_access_setting(db)
    setting.mode = payload.mode
    setting.updated_at = utcnow()
    setting.updated_by_id = auth.user.id
    current = client_ip(request)
    if payload.mode == "APPROVED":
        entries = list(db.scalars(select(AccessIPEntry).where(AccessIPEntry.list_mode == "APPROVED")))
        if not any(ip_value_matches(item.ip_value, current) for item in entries):
            db.add(AccessIPEntry(list_mode="APPROVED", ip_value=normalize_ip_value(current), note="Auto-added current administrator IP", created_by_id=auth.user.id))
    record_audit(db, request, admin_id=auth.user.id, action="IP_ACCESS_MODE_CHANGED", details=payload.mode)
    db.commit()
    return {"mode": setting.mode, "current_ip": current}


@router.post("/ip-access/entries", status_code=201)
def add_ip_access_entry(
    payload: AccessIPCreate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        normalized = normalize_ip_value(payload.ip_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    setting = get_access_setting(db)
    current = client_ip(request)
    list_mode = setting.mode
    if list_mode == "ALL":
        raise HTTPException(status_code=400, detail="Switch to APPROVED or REJECTED mode before adding IP entries.")
    if list_mode == "REJECTED" and ip_value_matches(normalized, current):
        raise HTTPException(status_code=400, detail="You cannot reject the IP address currently used by this administrator session.")
    existing = db.scalar(select(AccessIPEntry).where(AccessIPEntry.list_mode == list_mode, AccessIPEntry.ip_value == normalized))
    if existing is not None:
        raise HTTPException(status_code=409, detail="This IP address or network is already listed for the current mode.")
    item = AccessIPEntry(list_mode=list_mode, ip_value=normalized, note=payload.note.strip(), created_by_id=auth.user.id)
    db.add(item)
    db.flush()
    record_audit(db, request, admin_id=auth.user.id, action="IP_ACCESS_ENTRY_ADDED", details=normalized)
    db.commit()
    return {"id": item.id, "list_mode": item.list_mode, "ip_value": item.ip_value}


@router.delete("/ip-access/entries/{entry_id}")
def delete_ip_access_entry(
    entry_id: int,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(AccessIPEntry, entry_id)
    if item is None:
        raise HTTPException(status_code=404, detail="IP entry not found.")
    setting = get_access_setting(db)
    current = client_ip(request)
    if setting.mode == "APPROVED" and item.list_mode == "APPROVED" and ip_value_matches(item.ip_value, current):
        others = list(db.scalars(select(AccessIPEntry).where(AccessIPEntry.list_mode == "APPROVED", AccessIPEntry.id != item.id)))
        if not any(ip_value_matches(other.ip_value, current) for other in others):
            raise HTTPException(status_code=400, detail="Cannot remove the only Approved-list rule that currently allows this administrator IP.")
    value = item.ip_value
    db.delete(item)
    record_audit(db, request, admin_id=auth.user.id, action="IP_ACCESS_ENTRY_DELETED", details=value)
    db.commit()
    return {"status": "deleted"}


@router.get("/problems")
def admin_problems(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    problems = list(db.scalars(select(Problem).order_by(Problem.id.desc())))
    return [
        {
            "id": p.id,
            "title": p.title,
            "active": p.active,
            "accepting_submissions": p.accepting_submissions,
            "is_practice": p.is_practice,
            "time_limit_ms": p.time_limit_ms,
            "memory_limit_mb": p.memory_limit_mb,
            "exam_mode_enabled": p.exam_mode_enabled,
            "exam_duration_minutes": p.exam_duration_minutes,
            "submission_limit": p.submission_limit,
            "test_count": int(db.scalar(select(func.count(TestCase.id)).where(TestCase.problem_id == p.id)) or 0),
            "max_score": float(db.scalar(select(func.coalesce(func.sum(TestCase.points), 0.0)).where(TestCase.problem_id == p.id)) or 0.0),
        }
        for p in problems
    ]


@router.get("/problems/{problem_id}/student-access-check")
def problem_student_access_check(
    problem_id: int,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Show the exact student-facing availability state for an admin test.

    Student problem listing, direct problem access and submission creation all use
    the Problem.active gate. This endpoint lets an administrator verify that
    state without activating the problem.
    """
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found.")
    visible = bool(problem.active)
    submittable = bool(problem.active and problem.accepting_submissions)
    return {
        "problem_id": problem.id,
        "title": problem.title,
        "active": problem.active,
        "accepting_submissions": problem.accepting_submissions,
        "is_practice": problem.is_practice,
        "student_listed": visible,
        "student_direct_access": visible,
        "student_submission_allowed": submittable,
        "student_manual_run_allowed": visible,
        "admin_testing_allowed": True,
    }


@router.post("/problems", status_code=201)
def create_problem(
    payload: ProblemCreate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    problem = Problem(**payload.model_dump(), created_by=auth.user.id)
    db.add(problem)
    db.flush()
    record_audit(db, request, admin_id=auth.user.id, action="PROBLEM_CREATED", details=f"{problem.id}:{problem.title}")
    db.commit()
    return {"id": problem.id}


@router.patch("/problems/{problem_id}")
def update_problem(
    problem_id: int,
    payload: ProblemUpdate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found.")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(problem, key, value)
    record_audit(db, request, admin_id=auth.user.id, action="PROBLEM_UPDATED", details=f"{problem_id}:{values}")
    db.commit()
    return {"status": "updated"}


@router.post("/problems/{problem_id}/copy", status_code=201)
def copy_problem(
    problem_id: int,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    source = db.get(Problem, problem_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Problem not found.")

    clone = Problem(
        title=f"{source.title} (Copy)",
        statement=source.statement,
        input_format=source.input_format,
        output_format=source.output_format,
        constraints=source.constraints,
        time_limit_ms=source.time_limit_ms,
        memory_limit_mb=source.memory_limit_mb,
        active=False,
        accepting_submissions=False,
        is_practice=source.is_practice,
        exam_mode_enabled=source.exam_mode_enabled,
        exam_duration_minutes=source.exam_duration_minutes,
        submission_limit=source.submission_limit,
        created_by=auth.user.id,
    )
    db.add(clone)
    db.flush()

    source_tests = list(
        db.scalars(
            select(TestCase)
            .where(TestCase.problem_id == source.id)
            .order_by(TestCase.position.asc())
        )
    )
    for test in source_tests:
        db.add(
            TestCase(
                problem_id=clone.id,
                position=test.position,
                input_data=test.input_data,
                expected_output=test.expected_output,
                is_public=test.is_public,
                points=test.points,
            )
        )

    record_audit(
        db,
        request,
        admin_id=auth.user.id,
        action="PROBLEM_COPIED",
        details=f"source={source.id},copy={clone.id},tests={len(source_tests)}",
    )
    db.commit()
    return {
        "id": clone.id,
        "source_problem_id": source.id,
        "title": clone.title,
        "active": clone.active,
        "accepting_submissions": clone.accepting_submissions,
        "is_practice": clone.is_practice,
        "copied_tests": len(source_tests),
    }


@router.get("/problems/{problem_id}/tests")
def list_tests(problem_id: int, _auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    if db.get(Problem, problem_id) is None:
        raise HTTPException(status_code=404, detail="Problem not found.")
    return [
        {
            "id": t.id,
            "position": t.position,
            "input_data": t.input_data,
            "expected_output": t.expected_output,
            "is_public": t.is_public,
            "points": t.points,
        }
        for t in db.scalars(select(TestCase).where(TestCase.problem_id == problem_id).order_by(TestCase.position))
    ]


@router.post("/problems/{problem_id}/tests", status_code=201)
def create_test(
    problem_id: int,
    payload: TestCaseCreate,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(Problem, problem_id) is None:
        raise HTTPException(status_code=404, detail="Problem not found.")
    existing = db.scalar(select(TestCase).where(TestCase.problem_id == problem_id, TestCase.position == payload.position))
    if existing:
        raise HTTPException(status_code=409, detail="A test already uses this position.")
    test = TestCase(problem_id=problem_id, **payload.model_dump())
    db.add(test)
    db.flush()
    record_audit(db, request, admin_id=auth.user.id, action="TEST_CREATED", details=f"problem={problem_id},test={test.id}")
    db.commit()
    return {"id": test.id}


@router.delete("/tests/{test_id}")
def delete_test(
    test_id: int,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    test = db.get(TestCase, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found.")
    referenced = db.scalar(select(func.count(TestResult.id)).where(TestResult.test_case_id == test_id)) or 0
    if referenced:
        raise HTTPException(status_code=409, detail="Test is referenced by existing reports and cannot be deleted.")
    db.delete(test)
    record_audit(db, request, admin_id=auth.user.id, action="TEST_DELETED", details=str(test_id))
    db.commit()
    return {"status": "deleted"}


@router.get("/submissions/summary")
def admin_submission_summary(
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(
            Submission.user_id,
            func.count(Submission.id).label("submission_count"),
            func.count(func.distinct(Submission.problem_id)).label("problems_attempted"),
            func.max(Submission.created_at).label("latest_at"),
            func.max(Submission.score).label("best_automatic_score"),
        )
        .join(User, User.id == Submission.user_id)
        .where(User.role == "student")
        .group_by(Submission.user_id)
        .order_by(func.count(Submission.id).desc(), func.max(Submission.created_at).desc())
    ).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r.user_id for r in rows])))} if rows else {}
    return [
        {
            "user_id": row.user_id,
            "student": users[row.user_id].full_name if row.user_id in users else "",
            "enrollment_number": users[row.user_id].enrollment_number if row.user_id in users else "",
            "email": users[row.user_id].email if row.user_id in users else "",
            "submission_count": int(row.submission_count or 0),
            "problems_attempted": int(row.problems_attempted or 0),
            "best_automatic_score": float(row.best_automatic_score or 0.0),
            "latest_at": row.latest_at.isoformat() + "Z" if row.latest_at else None,
        }
        for row in rows
    ]


@router.get("/submissions/user/{user_id}")
def admin_user_submissions(
    user_id: int,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    items = list(
        db.scalars(
            select(Submission)
            .where(Submission.user_id == user_id)
            .order_by(Submission.created_at.desc())
            .limit(5000)
        )
    )
    return {
        "user": user_row(user, db),
        "submissions": [
            {
                "id": item.id,
                "problem_id": item.problem_id,
                "problem_title": item.problem.title,
                "language": item.language,
                "status": item.status,
                "verdict": item.verdict,
                "automatic_score": item.score,
                "manual_score": item.manual_score,
                "final_score": item.final_score,
                "official": item.official,
                "compile_message": item.compile_message,
                "source_code": item.source_code,
                "created_at": item.created_at.isoformat() + "Z",
                "finished_at": item.finished_at.isoformat() + "Z" if item.finished_at else None,
            }
            for item in items
        ],
    }


@router.get("/submissions")
def admin_submissions(
    user_id: int | None = None,
    problem_id: int | None = None,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(Submission).order_by(Submission.created_at.desc()).limit(2000)
    if user_id is not None:
        query = query.where(Submission.user_id == user_id)
    if problem_id is not None:
        query = query.where(Submission.problem_id == problem_id)
    users = {u.id: u for u in db.scalars(select(User))}
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "student": users[s.user_id].full_name if s.user_id in users else "",
            "enrollment_number": users[s.user_id].enrollment_number if s.user_id in users else "",
            "email": users[s.user_id].email if s.user_id in users else "",
            "problem_id": s.problem_id,
            "problem_title": s.problem.title,
            "language": s.language,
            "status": s.status,
            "verdict": s.verdict,
            "automatic_score": s.score,
            "manual_score": s.manual_score,
            "final_score": s.final_score,
            "official": s.official,
            "created_at": s.created_at.isoformat() + "Z",
        }
        for s in db.scalars(query)
    ]


@router.post("/submissions/{submission_id}/grade")
def grade_submission(
    submission_id: str,
    payload: ManualGrade,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(Submission, submission_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    max_score = float(db.scalar(select(func.coalesce(func.sum(TestCase.points), 0.0)).where(TestCase.problem_id == item.problem_id)) or 0.0)
    if payload.manual_score is not None and payload.manual_score > max_score:
        raise HTTPException(status_code=400, detail=f"Manual score cannot exceed {max_score}.")
    old = item.manual_score
    item.manual_score = payload.manual_score
    item.manual_notes = payload.notes
    item.graded_by_id = auth.user.id
    item.graded_at = utcnow()
    record_audit(db, request, admin_id=auth.user.id, target_user_id=item.user_id, action="MANUAL_GRADE_CHANGED", details=f"submission={item.id},old={old},new={payload.manual_score}")
    db.commit()
    return {"automatic_score": item.score, "manual_score": item.manual_score, "final_score": item.final_score}


@router.post("/submissions/{submission_id}/official")
def select_official(
    submission_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(Submission, submission_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    db.execute(
        update(Submission)
        .where(Submission.user_id == item.user_id, Submission.problem_id == item.problem_id)
        .values(official=False)
    )
    item.official = True
    record_audit(db, request, admin_id=auth.user.id, target_user_id=item.user_id, action="OFFICIAL_SUBMISSION_SELECTED", details=item.id)
    db.commit()
    return {"status": "selected"}


@router.get("/flags/summary")
def flags_summary(
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(
            ExamViolationSummary.user_id,
            func.sum(ExamViolationSummary.violation_count).label("violation_count"),
            func.count(ExamViolationSummary.id).label("session_count"),
            func.max(ExamViolationSummary.latest_at).label("latest_at"),
        )
        .group_by(ExamViolationSummary.user_id)
        .order_by(func.sum(ExamViolationSummary.violation_count).desc(), func.max(ExamViolationSummary.latest_at).desc())
    ).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([row.user_id for row in rows])))} if rows else {}
    return [
        {
            "user_id": row.user_id,
            "student": users[row.user_id].full_name if row.user_id in users else "",
            "enrollment_number": users[row.user_id].enrollment_number if row.user_id in users else "",
            "email": users[row.user_id].email if row.user_id in users else "",
            "violation_count": int(row.violation_count or 0),
            "session_count": int(row.session_count or 0),
            "latest_at": row.latest_at.isoformat() + "Z" if row.latest_at else None,
        }
        for row in rows
    ]


@router.get("/flags")
def flags(
    user_id: int | None = None,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cutoff = utcnow() - timedelta(hours=5)
    db.execute(delete(ExamFlag).where(ExamFlag.created_at < cutoff))
    db.commit()

    detail_query = select(ExamFlag).where(ExamFlag.created_at >= cutoff).order_by(ExamFlag.created_at.desc()).limit(5000)
    summary_query = select(ExamViolationSummary).order_by(ExamViolationSummary.latest_at.desc()).limit(5000)
    if user_id is not None:
        detail_query = detail_query.where(ExamFlag.user_id == user_id)
        summary_query = summary_query.where(ExamViolationSummary.user_id == user_id)
    users = {u.id: u for u in db.scalars(select(User))}
    details = [
        {
            "id": f.id,
            "user_id": f.user_id,
            "student": users[f.user_id].full_name if f.user_id in users else "",
            "enrollment_number": users[f.user_id].enrollment_number if f.user_id in users else "",
            "email": users[f.user_id].email if f.user_id in users else "",
            "problem_id": f.problem_id,
            "submission_id": f.submission_id,
            "generation_id": f.generation_id,
            "session_id": f.session_id,
            "event_type": f.event_type,
            "details": f.details,
            "client_ip": f.client_ip,
            "created_at": f.created_at.isoformat() + "Z",
        }
        for f in db.scalars(detail_query)
    ]
    sessions = []
    now = utcnow()
    for x in db.scalars(summary_query):
        started_at = x.first_at
        ended_at = None
        end_reason = ""
        status = "UNKNOWN"
        session_key = x.session_key
        if session_key.startswith("AUTH:"):
            try:
                auth_session = db.get(AuthSession, int(session_key.split(":", 1)[1]))
            except (TypeError, ValueError):
                auth_session = None
            if auth_session is not None:
                started_at = auth_session.exam_access_granted_at or auth_session.activated_at or auth_session.created_at
                ended_at = auth_session.exam_ended_at
                end_reason = auth_session.exam_end_reason or auth_session.revoke_reason or ""
                status = "ACTIVE" if ended_at is None and auth_session.state == "ACTIVE" else "ENDED"
        else:
            legacy = db.get(ExamSession, session_key)
            if legacy is not None:
                started_at = legacy.started_at
                ended_at = legacy.ended_at
                status = "ACTIVE" if legacy.active and legacy.ended_at is None else "ENDED"
                end_reason = "LEGACY_SESSION"
        duration_end = ended_at or now
        duration_seconds = max(0, int((duration_end - started_at).total_seconds())) if started_at else 0
        try:
            event_counts = json.loads(x.event_counts_json or "{}")
        except (TypeError, ValueError):
            event_counts = {}
        sessions.append({
            "id": x.id,
            "generation_id": x.generation_id,
            "session_key": x.session_key,
            "user_id": x.user_id,
            "problem_id": x.problem_id,
            "violation_count": x.violation_count,
            "first_at": x.first_at.isoformat() + "Z",
            "latest_at": x.latest_at.isoformat() + "Z",
            "session_started_at": started_at.isoformat() + "Z" if started_at else None,
            "session_ended_at": ended_at.isoformat() + "Z" if ended_at else None,
            "duration_seconds": duration_seconds,
            "status": status,
            "end_reason": end_reason,
            "event_counts": event_counts,
        })
    return {"retention_hours": 5, "details": details, "sessions": sessions}


@router.get("/audit")
def audit(_auth: AuthContext = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": item.id,
            "admin_id": item.admin_id,
            "target_user_id": item.target_user_id,
            "action": item.action,
            "details": item.details,
            "client_ip": item.client_ip,
            "created_at": item.created_at.isoformat() + "Z",
        }
        for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(5000))
    ]


@router.get("/submissions/compare/{left_id}/{right_id}")
def compare_submissions(
    left_id: str,
    right_id: str,
    _auth: AuthContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    left = db.get(Submission, left_id)
    right = db.get(Submission, right_id)
    if left is None or right is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    if left.user_id != right.user_id or left.problem_id != right.problem_id:
        raise HTTPException(status_code=400, detail="Compare language solutions from the same student and problem.")
    diff = "".join(
        difflib.unified_diff(
            left.source_code.splitlines(keepends=True),
            right.source_code.splitlines(keepends=True),
            fromfile=f"{left.language}_solution",
            tofile=f"{right.language}_solution",
        )
    )
    return {
        "left": {"id": left.id, "language": left.language, "source_code": left.source_code},
        "right": {"id": right.id, "language": right.language, "source_code": right.source_code},
        "unified_diff": diff,
    }

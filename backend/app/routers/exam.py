from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    ExamAccessAttempt,
    ExamCodeUse,
    ExamFlag,
    ExamSession,
    ExamViolationSummary,
    Problem,
)
from ..schemas import ExamCodeRequest, FlagCreate
from ..security import (
    AuthContext,
    activate_session,
    get_auth_context,
    get_session_context,
    get_exam_setting,
    require_csrf,
    require_session_csrf,
    utcnow,
    verify_password,
)
from ..services.rate_limit import client_ip

router = APIRouter(prefix="/api/exam", tags=["exam"])


def record_attempt(db: Session, request: Request, auth: AuthContext, event_type: str, details: str = "") -> None:
    setting = get_exam_setting(db)
    db.add(
        ExamAccessAttempt(
            user_id=auth.user.id,
            session_id=auth.session.id,
            generation_id=setting.active_generation_id,
            event_type=event_type,
            details=details,
            client_ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:512],
        )
    )


@router.get("/status")
def status(auth: AuthContext = Depends(get_session_context), db: Session = Depends(get_db)) -> dict:
    setting = get_exam_setting(db)
    return {
        "enabled": setting.enabled,
        "exam_name": setting.exam_name,
        "starts_at": setting.starts_at.isoformat() + "Z" if setting.starts_at else None,
        "ends_at": setting.ends_at.isoformat() + "Z" if setting.ends_at else None,
        "code_required": auth.session.state == "PENDING_EXAM",
        "results_published": setting.results_published,
    }


@router.post("/verify-code")
def verify_code(
    payload: ExamCodeRequest,
    request: Request,
    auth: AuthContext = Depends(require_session_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if auth.user.role == "admin":
        activate_session(db, auth.user, auth.session)
        db.commit()
        return {"status": "ACTIVE"}
    if auth.session.state != "PENDING_EXAM":
        return {"status": "ACTIVE"}
    setting = get_exam_setting(db)
    if not setting.enabled:
        activate_session(db, auth.user, auth.session)
        db.commit()
        return {"status": "ACTIVE"}
    if not setting.active_generation_id or not setting.code_hash:
        record_attempt(db, request, auth, "INACTIVE_EXAM_CODE")
        db.commit()
        raise HTTPException(status_code=409, detail="The administrator has not generated an exam code.")
    if setting.starts_at and setting.starts_at > utcnow():
        record_attempt(db, request, auth, "LOGIN_OUTSIDE_EXAM_TIME", "Exam has not started")
        db.commit()
        raise HTTPException(status_code=403, detail="The exam has not started.")
    if setting.ends_at and setting.ends_at <= utcnow():
        record_attempt(db, request, auth, "LOGIN_OUTSIDE_EXAM_TIME", "Exam has ended")
        db.commit()
        raise HTTPException(status_code=403, detail="The exam has ended.")
    if setting.expires_at and setting.expires_at <= utcnow():
        record_attempt(db, request, auth, "EXPIRED_EXAM_CODE")
        db.commit()
        raise HTTPException(status_code=403, detail="The exam code has expired.")
    if not verify_password(setting.code_hash, payload.code):
        record_attempt(db, request, auth, "INVALID_EXAM_CODE")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid exam code.")

    existing = db.scalar(
        select(ExamCodeUse).where(
            ExamCodeUse.generation_id == setting.active_generation_id,
            ExamCodeUse.user_id == auth.user.id,
        )
    )
    if existing is not None:
        record_attempt(db, request, auth, "REUSED_EXAM_CODE")
        db.commit()
        raise HTTPException(status_code=409, detail="This exam code has already been used by your account. Ask the administrator to regenerate the exam code if re-entry is required.")
    db.add(
        ExamCodeUse(
            generation_id=setting.active_generation_id,
            user_id=auth.user.id,
            client_ip=client_ip(request),
        )
    )

    auth.session.exam_generation_id = setting.active_generation_id
    auth.session.exam_access_granted_at = utcnow()
    auth.session.exam_ended_at = None
    auth.session.exam_end_reason = ""
    activate_session(db, auth.user, auth.session)
    record_attempt(db, request, auth, "EXAM_CODE_ACCEPTED")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This exam code was already used.") from None
    return {"status": "ACTIVE"}


@router.post("/sessions/start")
def start_exam_session(
    problem_id: int,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or not problem.active:
        raise HTTPException(status_code=404, detail="Problem not found.")
    existing = db.scalar(
        select(ExamSession)
        .where(ExamSession.user_id == auth.user.id, ExamSession.problem_id == problem_id, ExamSession.active.is_(True))
        .order_by(ExamSession.started_at.desc())
    )
    if existing:
        existing.last_seen_at = utcnow()
        db.commit()
        return {"session_id": existing.id, "started_at": existing.started_at.isoformat() + "Z"}
    session = ExamSession(
        id=str(uuid.uuid4()),
        user_id=auth.user.id,
        problem_id=problem_id,
        client_ip=client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:512],
    )
    db.add(session)
    db.commit()
    return {"session_id": session.id, "started_at": session.started_at.isoformat() + "Z"}


@router.post("/sessions/{session_id}/heartbeat")
def heartbeat(
    session_id: str,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    item = db.get(ExamSession, session_id)
    if item is None or item.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Exam session not found.")
    item.last_seen_at = utcnow()
    db.commit()
    return {"status": "ok"}


@router.post("/flags", status_code=201)
def create_flag(
    payload: FlagCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    allowed = {
        "TAB_SWITCH", "WINDOW_BLUR", "FULLSCREEN_EXIT", "COPY_ATTEMPT", "CUT_ATTEMPT",
        "PASTE_ATTEMPT", "RIGHT_CLICK", "F12_ATTEMPT", "DEVTOOLS_SHORTCUT",
        "VIEW_SOURCE_ATTEMPT", "PRINT_ATTEMPT", "IP_CHANGED", "BROWSER_CHANGED",
    }
    event = payload.event_type.strip().upper()
    if event not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported flag type.")
    if payload.session_id:
        exam_session = db.get(ExamSession, payload.session_id)
        if exam_session is None or exam_session.user_id != auth.user.id:
            raise HTTPException(status_code=404, detail="Exam session not found.")
    setting = get_exam_setting(db)
    if not setting.enabled:
        return {"status": "ignored", "violation_count": 0}
    generation_id = setting.active_generation_id or "NO_GENERATION"
    now = utcnow()
    db.add(
        ExamFlag(
            session_id=payload.session_id,
            user_id=auth.user.id,
            problem_id=payload.problem_id,
            submission_id=payload.submission_id,
            generation_id=generation_id,
            event_type=event,
            details=payload.details,
            client_timestamp=payload.client_timestamp,
            client_ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:512],
            created_at=now,
        )
    )

    session_key = f"AUTH:{auth.session.id}"
    summary = db.scalar(
        select(ExamViolationSummary).where(
            ExamViolationSummary.generation_id == generation_id,
            ExamViolationSummary.session_key == session_key,
            ExamViolationSummary.user_id == auth.user.id,
        )
    )
    if summary is None:
        summary = ExamViolationSummary(
            generation_id=generation_id,
            session_key=session_key,
            user_id=auth.user.id,
            problem_id=payload.problem_id,
            violation_count=1,
            first_at=now,
            latest_at=now,
            event_counts_json=json.dumps({event: 1}, sort_keys=True),
        )
        db.add(summary)
    else:
        summary.violation_count += 1
        summary.latest_at = now
        if payload.problem_id is not None:
            summary.problem_id = payload.problem_id
        try:
            event_counts = json.loads(summary.event_counts_json or "{}")
        except (TypeError, ValueError):
            event_counts = {}
        event_counts[event] = int(event_counts.get(event, 0)) + 1
        summary.event_counts_json = json.dumps(event_counts, sort_keys=True)

    db.flush()
    violation_count = int(
        db.scalar(
            select(func.coalesce(func.sum(ExamViolationSummary.violation_count), 0)).where(
                ExamViolationSummary.user_id == auth.user.id,
                ExamViolationSummary.generation_id == generation_id,
            )
        )
        or 0
    )
    db.commit()
    return {"status": "recorded", "violation_count": violation_count}


@router.get("/flags/count")
def flag_count(
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    setting = get_exam_setting(db)
    if not setting.enabled:
        return {"enabled": False, "violation_count": 0}
    generation_id = setting.active_generation_id or "NO_GENERATION"
    query = select(func.coalesce(func.sum(ExamViolationSummary.violation_count), 0)).where(
        ExamViolationSummary.user_id == auth.user.id,
        ExamViolationSummary.generation_id == generation_id,
    )
    return {
        "enabled": True,
        "generation_id": setting.active_generation_id,
        "violation_count": int(db.scalar(query) or 0),
    }

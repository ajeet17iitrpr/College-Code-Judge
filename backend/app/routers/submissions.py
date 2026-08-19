from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..languages import LANGUAGES
from ..models import ExamSession, Problem, Submission, TestCase, TestResult
from ..security import AuthContext, get_auth_context, get_exam_setting, require_csrf, utcnow
from ..services.submission_queue import notify_submission

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


def access_submission(submission_id: str, auth: AuthContext, db: Session) -> Submission:
    item = db.get(Submission, submission_id)
    if item is None or (auth.user.role != "admin" and item.user_id != auth.user.id):
        raise HTTPException(status_code=404, detail="Submission not found.")
    return item


def submission_summary(item: Submission) -> dict:
    return {
        "id": item.id,
        "problem_id": item.problem_id,
        "problem_title": item.problem.title if item.problem else "",
        "language": item.language,
        "filename": item.filename,
        "status": item.status,
        "verdict": item.verdict,
        "automatic_score": item.score,
        "manual_score": item.manual_score,
        "final_score": item.final_score,
        "official": item.official,
        "created_at": item.created_at.isoformat() + "Z",
    }


@router.post("", status_code=202)
async def submit_code(
    problem_id: Annotated[int, Form()],
    language: Annotated[str, Form()],
    exam_session_id: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    source_file: Annotated[UploadFile | None, File()] = None,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    problem = db.get(Problem, problem_id)
    is_admin_test = auth.user.role == "admin"
    # A deactivated problem remains completely unavailable to students, even by
    # guessing its ID. Administrators can still submit against the exact same
    # judge pipeline for pre-exam verification.
    if problem is None or (not problem.active and not is_admin_test):
        raise HTTPException(status_code=404, detail="Problem not found.")
    if not is_admin_test and not problem.accepting_submissions:
        raise HTTPException(status_code=403, detail="Submissions are currently closed for this problem. You may still read the problem and use Run Manual.")
    setting = get_exam_setting(db)
    if not is_admin_test and problem.is_practice and setting.enabled:
        raise HTTPException(status_code=403, detail="Practice problems are unavailable while Exam Mode is active.")
    if not is_admin_test and setting.enabled and setting.ends_at and setting.ends_at <= utcnow():
        raise HTTPException(status_code=403, detail="The exam has ended. New submissions are closed.")
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language.")
    if (code is None or not code.strip()) and source_file is None:
        raise HTTPException(status_code=400, detail="Provide source code or upload a file.")

    if exam_session_id and not is_admin_test:
        session = db.get(ExamSession, exam_session_id)
        if session is None or session.user_id != auth.user.id or session.problem_id != problem_id:
            raise HTTPException(status_code=400, detail="Invalid exam session.")

    last_submission = db.scalar(
        select(Submission)
        .where(Submission.user_id == auth.user.id, Submission.problem_id == problem_id)
        .order_by(Submission.created_at.desc())
    )
    if (
        not is_admin_test
        and last_submission
        and last_submission.created_at > utcnow() - timedelta(seconds=settings.min_submission_delay_seconds)
    ):
        raise HTTPException(status_code=429, detail="Please wait a few seconds before submitting again.")
    language_count = db.scalar(
        select(func.count(Submission.id)).where(Submission.user_id == auth.user.id, Submission.problem_id == problem_id)
    ) or 0
    existing_submission = db.scalar(
        select(Submission).where(
            Submission.user_id == auth.user.id,
            Submission.problem_id == problem_id,
            Submission.language == language,
        )
    )
    if existing_submission is not None and existing_submission.status in {"QUEUED", "COMPILING", "RUNNING"}:
        raise HTTPException(status_code=409, detail="This language solution is still being judged. Wait for it to finish before submitting an update.")
    # One solution is stored for each student + problem + language. A later
    # submit in the same language replaces the existing code and judge result.
    if (
        not is_admin_test
        and existing_submission is None
        and problem.submission_limit is not None
        and language_count >= problem.submission_limit
    ):
        raise HTTPException(status_code=403, detail="Submission-language limit reached for this problem.")

    cfg = LANGUAGES[language]
    if source_file is not None:
        raw = await source_file.read(settings.max_source_bytes + 1)
        if len(raw) > settings.max_source_bytes:
            raise HTTPException(status_code=413, detail="Source file is too large.")
        try:
            source_code = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Source file must be UTF-8.") from None
        supplied_name = Path(source_file.filename or "").name
    else:
        source_code = code or ""
        supplied_name = str(cfg["filename"])
    if len(source_code.encode("utf-8")) > settings.max_source_bytes or "\x00" in source_code:
        raise HTTPException(status_code=400, detail="Invalid or oversized source code.")
    if Path(supplied_name).suffix.lower() not in cfg["extensions"]:
        raise HTTPException(status_code=400, detail="File extension does not match selected language.")
    test_count = db.scalar(select(func.count(TestCase.id)).where(TestCase.problem_id == problem_id)) or 0
    if test_count == 0:
        raise HTTPException(status_code=409, detail="This problem has no test cases.")

    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", supplied_name)[:128] or str(cfg["filename"])
    now = utcnow()
    replaced = existing_submission is not None
    if existing_submission is None:
        # Keep the legacy internal version column as a stable language slot so
        # older databases with the user/problem/version uniqueness constraint
        # remain compatible. It is no longer presented as a student version.
        language_slot = {"c": 1, "cpp": 2, "python": 3, "java": 4}[language]
        item = Submission(
            id=str(uuid.uuid4()),
            user_id=auth.user.id,
            problem_id=problem_id,
            exam_session_id=None if is_admin_test else exam_session_id,
            version=language_slot,
            language=language,
            filename=safe_name,
            source_code=source_code,
            status="QUEUED",
            created_at=now,
        )
        db.add(item)
    else:
        item = existing_submission
        db.execute(delete(TestResult).where(TestResult.submission_id == item.id))
        item.exam_session_id = None if is_admin_test else exam_session_id
        item.filename = safe_name
        item.source_code = source_code
        item.status = "QUEUED"
        item.verdict = None
        item.score = 0.0
        item.manual_score = None
        item.manual_notes = ""
        item.graded_by_id = None
        item.graded_at = None
        item.official = False
        item.compile_message = ""
        item.created_at = now
        item.started_at = None
        item.finished_at = None
    db.commit()
    notify_submission(item.id)
    return {"submission_id": item.id, "language": language, "replaced": replaced, "status": "QUEUED"}


@router.post("/manual-run")
def manual_run_code(
    problem_id: Annotated[int, Form()],
    language: Annotated[str, Form()],
    stdin_data: Annotated[str, Form()] = "",
    exam_session_id: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    """Run the current source once with user-provided stdin.

    Manual runs do not create/update Submission rows, do not award marks, and do
    not read public or private test-case input.
    """
    problem = db.get(Problem, problem_id)
    is_admin_test = auth.user.role == "admin"
    if problem is None or (not problem.active and not is_admin_test):
        raise HTTPException(status_code=404, detail="Problem not found.")

    setting = get_exam_setting(db)
    if not is_admin_test and problem.is_practice and setting.enabled:
        raise HTTPException(status_code=403, detail="Practice problems are unavailable while Exam Mode is active.")
    if not is_admin_test and setting.enabled and setting.ends_at and setting.ends_at <= utcnow():
        raise HTTPException(status_code=403, detail="The exam has ended. Manual runs are closed.")

    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language.")
    source_code = code or ""
    if not source_code.strip():
        raise HTTPException(status_code=400, detail="Provide source code to run.")
    if len(source_code.encode("utf-8")) > settings.max_source_bytes or "\x00" in source_code:
        raise HTTPException(status_code=400, detail="Invalid or oversized source code.")
    if len(stdin_data.encode("utf-8")) > settings.max_test_bytes or "\x00" in stdin_data:
        raise HTTPException(status_code=400, detail="Manual input is too large or invalid.")

    if exam_session_id and not is_admin_test:
        session = db.get(ExamSession, exam_session_id)
        if session is None or session.user_id != auth.user.id or session.problem_id != problem_id:
            raise HTTPException(status_code=400, detail="Invalid exam session.")

    # The backend normally starts from PROJECT_ROOT/backend, so make the trusted
    # sibling judge package importable without changing global deployment paths.
    import sys
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from judge.executor import ManualRunJob, execute_manual

    cfg = LANGUAGES[language]
    outcome = execute_manual(
        ManualRunJob(
            source_code=source_code,
            language=language,
            filename=str(cfg["filename"]),
            stdin_data=stdin_data,
            time_limit_ms=problem.time_limit_ms,
            memory_limit_mb=problem.memory_limit_mb,
        )
    )
    return {
        "mode": "manual",
        "verdict": outcome.verdict,
        "compile_message": outcome.compile_message,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "runtime_ms": outcome.runtime_ms,
        "memory_kb": outcome.memory_kb,
        "saved": False,
        "scored": False,
    }


@router.get("")
def list_submissions(
    problem_id: int | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(Submission).order_by(Submission.created_at.desc()).limit(500)
    if auth.user.role != "admin":
        query = query.where(Submission.user_id == auth.user.id)
    if problem_id is not None:
        query = query.where(Submission.problem_id == problem_id)
    return [submission_summary(item) for item in db.scalars(query)]


@router.get("/{submission_id}")
def get_submission(
    submission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    item = access_submission(submission_id, auth, db)
    is_admin = auth.user.role == "admin"
    results = list(db.scalars(select(TestResult).where(TestResult.submission_id == item.id).order_by(TestResult.position)))
    total_points = db.scalar(
        select(func.coalesce(func.sum(TestCase.points), 0.0)).where(TestCase.problem_id == item.problem_id)
    ) or 0.0
    public_results = []
    private_counts: dict[str, int] = {}
    all_results = []
    for result in results:
        private_counts[result.verdict] = private_counts.get(result.verdict, 0) + (0 if result.is_public else 1)
        payload = {
            "position": result.position,
            "visibility": "public" if result.is_public else "private",
            "verdict": result.verdict,
            "runtime_ms": result.runtime_ms,
            "memory_kb": result.memory_kb,
            "points_awarded": result.points_awarded,
            "actual_output": result.actual_output if result.is_public or is_admin else None,
            "expected_output": result.expected_output if result.is_public or is_admin else None,
            "stderr": result.stderr if result.is_public or is_admin else None,
        }
        if result.is_public:
            public_results.append(payload)
        if is_admin:
            all_results.append(payload)
    response = {
        **submission_summary(item),
        "problem": {"id": item.problem.id, "title": item.problem.title},
        "total_points": float(total_points),
        "compile_message": item.compile_message,
        "manual_notes": item.manual_notes if is_admin else "",
        "public_results": public_results,
        "private_summary": private_counts,
        "private_total": sum(1 for result in results if not result.is_public),
        "private_passed": sum(1 for result in results if not result.is_public and result.verdict == "AC"),
    }
    if is_admin:
        response["results"] = all_results
        response["source_code"] = item.source_code
        response["user_id"] = item.user_id
    return response


@router.get("/{submission_id}/source")
def get_source(
    submission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    item = access_submission(submission_id, auth, db)
    return {
        "id": item.id,
        "language": item.language,
        "filename": item.filename,
        "source_code": item.source_code,
    }

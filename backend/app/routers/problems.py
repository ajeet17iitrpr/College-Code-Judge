from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Problem, Submission, TestCase
from ..security import AuthContext, get_auth_context, get_exam_setting

router = APIRouter(prefix="/api/problems", tags=["problems"])


@router.get("")
def list_problems(
    practice: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    # Practice problems are deliberately separated from the normal/exam problem
    # list. During Exam Mode, student practice access is hidden to keep the
    # exam workspace focused on the configured assessment problems.
    if auth.user.role != "admin" and practice and get_exam_setting(db).enabled:
        return []

    problems = list(
        db.scalars(
            select(Problem)
            .where(Problem.active.is_(True), Problem.is_practice.is_(practice))
            .order_by(Problem.id.asc())
        )
    )
    output = []
    for problem in problems:
        test_count = db.scalar(select(func.count(TestCase.id)).where(TestCase.problem_id == problem.id)) or 0
        total_points = db.scalar(select(func.coalesce(func.sum(TestCase.points), 0.0)).where(TestCase.problem_id == problem.id)) or 0.0
        attempts = db.scalar(
            select(func.count(Submission.id)).where(
                Submission.problem_id == problem.id,
                Submission.user_id == auth.user.id,
            )
        ) or 0
        best = db.scalar(
            select(func.coalesce(func.max(Submission.score), 0.0)).where(
                Submission.problem_id == problem.id,
                Submission.user_id == auth.user.id,
            )
        ) or 0.0
        output.append({
            "id": problem.id,
            "title": problem.title,
            "time_limit_ms": problem.time_limit_ms,
            "memory_limit_mb": problem.memory_limit_mb,
            "test_count": int(test_count),
            "max_score": float(total_points),
            "attempts": int(attempts),
            "best_score": float(best),
            "active": problem.active,
            "accepting_submissions": problem.accepting_submissions,
            "is_practice": problem.is_practice,
            "exam_mode_enabled": problem.exam_mode_enabled,
            "exam_duration_minutes": problem.exam_duration_minutes,
        })
    return output


@router.get("/{problem_id}")
def get_problem(
    problem_id: int,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    problem = db.get(Problem, problem_id)
    # Students can never open a deactivated problem directly. Administrators
    # may preview it so it can be tested safely before activation.
    if problem is None or (not problem.active and auth.user.role != "admin"):
        raise HTTPException(status_code=404, detail="Problem not found.")
    if auth.user.role != "admin" and problem.is_practice and get_exam_setting(db).enabled:
        raise HTTPException(status_code=404, detail="Problem not found.")
    public_tests = list(
        db.scalars(
            select(TestCase)
            .where(TestCase.problem_id == problem_id, TestCase.is_public.is_(True))
            .order_by(TestCase.position.asc())
        )
    )
    return {
        "id": problem.id,
        "title": problem.title,
        "active": problem.active,
        "accepting_submissions": problem.accepting_submissions,
        "is_practice": problem.is_practice,
        "statement": problem.statement,
        "input_format": problem.input_format,
        "output_format": problem.output_format,
        "constraints": problem.constraints,
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_mb": problem.memory_limit_mb,
        "exam_mode_enabled": problem.exam_mode_enabled,
        "exam_duration_minutes": problem.exam_duration_minutes,
        "submission_limit": problem.submission_limit,
        "public_tests": [
            {
                "position": item.position,
                "input_data": item.input_data,
                "expected_output": item.expected_output,
                "points": item.points,
            }
            for item in public_tests
        ],
    }

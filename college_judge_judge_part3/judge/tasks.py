from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update

from .celery_app import celery_app
from .config import BACKEND_DIR, settings
from .executor import SubmissionJob, TestSpec, execute_submission


if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import Problem, Submission, TestCase, TestResult  # noqa: E402


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _claim_job(submission_id: str) -> SubmissionJob | None:
    db = SessionLocal()
    try:
        claimed = db.execute(
            update(Submission)
            .where(
                Submission.id == submission_id,
                Submission.status == "QUEUED",
            )
            .values(status="RUNNING", started_at=_utcnow(), finished_at=None)
        )
        db.commit()
        if claimed.rowcount != 1:
            return None

        submission = db.get(Submission, submission_id)
        if submission is None:
            return None
        problem = db.get(Problem, submission.problem_id)
        if problem is None:
            raise RuntimeError("Problem no longer exists.")

        tests = tuple(
            TestSpec(
                id=test.id,
                position=test.position,
                input_data=test.input_data,
                expected_output=test.expected_output,
                is_public=test.is_public,
                points=float(test.points),
            )
            for test in db.scalars(
                select(TestCase)
                .where(TestCase.problem_id == problem.id)
                .order_by(TestCase.position.asc())
            )
        )
        if not tests:
            raise RuntimeError("No test cases are configured.")

        db.execute(delete(TestResult).where(TestResult.submission_id == submission_id))
        db.commit()

        return SubmissionJob(
            submission_id=submission.id,
            source_code=submission.source_code,
            language=submission.language,
            filename=submission.filename,
            time_limit_ms=int(problem.time_limit_ms),
            memory_limit_mb=int(problem.memory_limit_mb),
            tests=tests,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _persist_outcome(submission_id: str, outcome) -> None:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return

        db.execute(delete(TestResult).where(TestResult.submission_id == submission_id))
        for case in outcome.cases:
            db.add(
                TestResult(
                    submission_id=submission_id,
                    test_case_id=case.test_case_id,
                    position=case.position,
                    is_public=case.is_public,
                    verdict=case.verdict,
                    runtime_ms=case.runtime_ms,
                    memory_kb=case.memory_kb,
                    actual_output=case.actual_output,
                    expected_output=case.expected_output,
                    stderr=case.stderr,
                    points_awarded=case.points_awarded,
                )
            )

        submission.status = "FAILED" if outcome.system_failure else "COMPLETED"
        submission.verdict = outcome.verdict
        submission.score = outcome.score
        submission.compile_message = outcome.compile_message
        submission.finished_at = _utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _mark_system_failure(submission_id: str) -> None:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        submission.status = "FAILED"
        submission.verdict = "SE"
        submission.score = 0.0
        submission.compile_message = (
            "Internal judging error. Contact the administrator with the submission ID."
        )
        submission.finished_at = _utcnow()
        db.commit()
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="judge.tasks.judge_submission",
    acks_late=True,
    reject_on_worker_lost=True,
)
def judge_submission(self, submission_id: str) -> dict:
    try:
        job = _claim_job(submission_id)
        if job is None:
            return {"submission_id": submission_id, "status": "not-claimed"}
        outcome = execute_submission(job)
        _persist_outcome(submission_id, outcome)
        return {
            "submission_id": submission_id,
            "status": "finished",
            "verdict": outcome.verdict,
            "score": outcome.score,
        }
    except Exception:
        logger.exception("Judging failed for submission %s", submission_id)
        _mark_system_failure(submission_id)
        return {"submission_id": submission_id, "status": "failed", "verdict": "SE"}


@celery_app.task(name="judge.tasks.scan_queued_submissions")
def scan_queued_submissions() -> dict:
    db = SessionLocal()
    try:
        stale_before = _utcnow() - timedelta(seconds=settings.stale_submission_seconds)
        db.execute(
            update(Submission)
            .where(
                Submission.status == "RUNNING",
                Submission.started_at.is_not(None),
                Submission.started_at < stale_before,
            )
            .values(status="QUEUED", started_at=None)
        )
        queued_ids = list(
            db.scalars(
                select(Submission.id)
                .where(Submission.status == "QUEUED")
                .order_by(Submission.created_at.asc())
                .limit(settings.queued_scan_limit)
            )
        )
        db.commit()
    finally:
        db.close()

    for submission_id in queued_ids:
        celery_app.send_task(
            "judge.tasks.judge_submission",
            args=[submission_id],
            queue=settings.queue_name,
        )
    return {"queued": len(queued_ids)}

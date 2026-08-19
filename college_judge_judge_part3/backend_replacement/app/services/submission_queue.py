from __future__ import annotations

import logging
import os

from celery import Celery


logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
JUDGE_QUEUE = os.getenv("JUDGE_QUEUE", "judge")

_queue_client = Celery("college_judge_api", broker=REDIS_URL)
_queue_client.conf.update(
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
)


def notify_submission(submission_id: str) -> None:
    """
    Signal the judge worker after the database transaction has committed.

    If Redis is temporarily unavailable, the submission remains QUEUED. The
    judge worker's periodic recovery scan will enqueue it when Redis returns.
    """
    try:
        _queue_client.send_task(
            "judge.tasks.judge_submission",
            args=[submission_id],
            queue=JUDGE_QUEUE,
        )
    except Exception:
        logger.exception(
            "Could not notify the judge queue for submission %s; it remains QUEUED.",
            submission_id,
        )

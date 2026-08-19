from __future__ import annotations

import logging

from celery import Celery

from ..config import settings

logger = logging.getLogger(__name__)
_client = Celery("judge_dispatch", broker=settings.redis_url)


def notify_submission(submission_id: str) -> None:
    try:
        _client.send_task(
            "judge.tasks.judge_submission",
            args=[submission_id],
            queue=settings.judge_queue,
        )
    except Exception:
        # The periodic worker scan will recover queued submissions.
        logger.exception("Could not immediately dispatch submission %s", submission_id)

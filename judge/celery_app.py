from __future__ import annotations

from celery import Celery

from .config import settings


celery_app = Celery(
    "college_code_judge",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
    include=["judge.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.queue_name,
    task_routes={"judge.tasks.*": {"queue": settings.queue_name}},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "recover-and-dispatch-queued-submissions": {
            "task": "judge.tasks.scan_queued_submissions",
            "schedule": 10.0,
        },
        "purge-expired-exam-flag-details": {
            "task": "judge.tasks.purge_expired_exam_flag_details",
            "schedule": 600.0,
        }
    },
)

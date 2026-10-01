"""Celery application instance.

The worker process uses an independent SQLAlchemy session (sync driver) so it
doesn't share the FastAPI event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.signals import worker_ready

from app.config import get_settings

logger = logging.getLogger(__name__)


def _build_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "videotranscoder",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["app.workers.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_always_eager=settings.celery_task_always_eager,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app: Celery = _build_celery()


@worker_ready.connect  # type: ignore[misc]
def _on_worker_ready(sender: object = None, **kwargs: Any) -> None:  # pragma: no cover
    logger.info(
        "Celery worker ready", extra={"sender": getattr(sender, "hostname", "?")}
    )

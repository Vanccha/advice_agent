from __future__ import annotations

import asyncio
import logging
import random

from shared.app_factory import create_app

from app.db import get_session_factory, wait_ready
from app.settings import get_settings
from app.worker import run_once

logger = logging.getLogger(__name__)

settings = get_settings()
_rng = random.Random()
_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _tick() -> None:
    factory = get_session_factory()
    session = factory()
    try:
        run_once(session, settings, _rng)
    except Exception:  # pragma: no cover - the loop must keep running
        logger.exception("provisioning-worker tick failed")
        session.rollback()
    finally:
        session.close()


async def _loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        await asyncio.to_thread(_tick)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=settings.worker_interval_seconds)
        except asyncio.TimeoutError:
            pass


def _on_startup() -> None:
    wait_ready()
    global _worker_task, _stop_event
    _stop_event = asyncio.Event()
    _worker_task = asyncio.get_event_loop().create_task(_loop())


app = create_app(
    service_name=settings.service_name,
    version=settings.service_version,
    title="NetSwift provisioning-worker",
    description="Background provisioning job runner.",
    on_startup=_on_startup,
    log_level=settings.log_level,
)
# Note: shared.app_factory.create_app wires a custom lifespan with no shutdown hook,
# so the background loop task simply ends when the process is terminated (SIGTERM).
# That is acceptable here: ticks are short and idempotent, nothing to flush on exit.

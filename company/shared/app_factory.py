from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.errors import install_error_handlers
from shared.metrics import install_metrics


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )


def create_app(
    *,
    service_name: str,
    version: str,
    title: str,
    description: str = "",
    on_startup: Callable[[], None] | None = None,
    log_level: str = "INFO",
) -> FastAPI:
    """Create a FastAPI app with the house standards: health, metrics, error envelope."""
    configure_logging(log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        if on_startup is not None:
            on_startup()
        yield

    app = FastAPI(
        title=title,
        version=version,
        description=description,
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
    )
    install_error_handlers(app)
    install_metrics(app, service_name)

    @app.get("/health", tags=["ops"], include_in_schema=True)
    def health() -> dict[str, str]:
        return {"status": "ok", "service": service_name, "version": version}

    return app

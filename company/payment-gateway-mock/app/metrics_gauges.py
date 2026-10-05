from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram

psp_charges_total = Counter(
    "psp_charges_total",
    "Charges processed by the PSP simulator, by resulting status.",
    ["status"],
)
psp_refunds_total = Counter(
    "psp_refunds_total",
    "Refunds processed by the PSP simulator, by resulting status.",
    ["status"],
)
psp_request_duration_seconds = Histogram(
    "psp_request_duration_seconds",
    "Request duration observed by the PSP simulator.",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
psp_outage = Gauge(
    "psp_outage",
    "1 when the PSP simulator's control flag has outage enabled, else 0.",
)


def set_outage_gauge(active: bool) -> None:
    psp_outage.set(1 if active else 0)


def install_psp_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def _psp_timing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        psp_request_duration_seconds.labels(request.method, path).observe(
            time.perf_counter() - started
        )
        return response

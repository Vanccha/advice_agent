from __future__ import annotations

import time

from fastapi import APIRouter, FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "netswift_http_requests_total",
    "HTTP requests handled by a NetSwift service.",
    ["service", "method", "path", "status"],
)
LATENCY = Histogram(
    "netswift_http_request_duration_seconds",
    "HTTP request duration.",
    ["service", "method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def install_metrics(app: FastAPI, service_name: str) -> None:
    app.include_router(router)

    @app.middleware("http")
    async def _collect(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUESTS.labels(service_name, request.method, path, str(response.status_code)).inc()
        LATENCY.labels(service_name, request.method, path).observe(time.perf_counter() - started)
        return response

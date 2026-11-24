"""Operational endpoints for orchestrators and monitoring: `/health` and `/metrics`.

Where this sits: HTTP layer. Called by Docker/Kubernetes health checks and Prometheus.

Key ideas:
- Liveness, not readiness: `/health` says "the process is up" even when the assistant is
  degraded, so a missing LLM key does not make the platform restart-loop the container.
- `/metrics` exposes the default Prometheus registry in the text exposition format.
- Both are unauthenticated by design; they carry no customer data.

Read next: `observability/` for tracing.
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/health")
def health():
    """Always 200 while the process serves requests."""
    return {"status": "ok", "service": "assistant", "version": "0.1.0"}


@router.get("/metrics")
def metrics():
    """Prometheus scrape target."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

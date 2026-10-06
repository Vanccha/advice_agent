from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PROVISIONING_JOBS_TOTAL = Counter(
    "netswift_provisioning_jobs_total", "Provisioning jobs processed, by terminal/claim status.", ["status"]
)
PROVISIONING_JOBS_STUCK = Gauge(
    "netswift_provisioning_jobs_stuck", "Provisioning jobs currently flagged stuck."
)
PROVISIONING_JOB_DURATION_SECONDS = Histogram(
    "netswift_provisioning_job_duration_seconds",
    "Wall-clock time spent processing a provisioning job.",
    buckets=(0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15),
)
WORKER_SWEEPS_TOTAL = Counter(
    "netswift_worker_sweeps_total", "Number of stuck-job sweep passes performed by the worker."
)

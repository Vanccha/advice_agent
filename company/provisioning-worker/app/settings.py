from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class WorkerSettings(CompanySettings):
    """Settings for the provisioning-worker service."""

    service_name: str = "provisioning-worker"
    service_version: str = "1.0.0"

    provision_success_rate: float = 0.9
    worker_interval_seconds: float = 5.0
    stuck_after_seconds: float = 300.0

    min_work_seconds: float = 2.0
    max_work_seconds: float = 6.0


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()

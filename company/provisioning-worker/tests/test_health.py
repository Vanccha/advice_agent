from __future__ import annotations


def test_health_and_metrics_do_not_require_db_to_import():
    """Importing the worker and metrics modules must not require a live DB connection."""
    from app import metrics, models, settings, worker  # noqa: F401

    assert settings.get_settings().service_name == "provisioning-worker"

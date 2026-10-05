from __future__ import annotations

import types

import pytest

from observability import fallback, tracing


@pytest.fixture(autouse=True)
def _reset_observability_state():
    tracing.reset_tracing_for_tests()
    fallback.reset_warnings_for_tests()
    yield
    tracing.reset_tracing_for_tests()
    fallback.reset_warnings_for_tests()


def make_settings(**overrides) -> types.SimpleNamespace:
    base = dict(
        TENANT="nethiz",
        LANGFUSE_ENABLED=False,
        LANGFUSE_PUBLIC_KEY=None,
        LANGFUSE_SECRET_KEY=None,
        LANGFUSE_BASE_URL=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_common.config import clear_tenant_config_cache
from core_common.db import get_engine
from core_common.settings import get_settings
from decision.llm_structured import LLMStructuredDecisionService
from llm.scripted import ScriptedProvider, ScriptedRule
from mcp_gateway.fake import FakeGateway
from modes.orchestrator import Orchestrator
from modes.tests.conftest import default_responses

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

INTENT_RULES = [
    ScriptedRule(match=r"package|recommend|suggest", structured={"_IntentResult": {"value": "advisory", "confidence": 0.95, "rationale": "scripted"}}),
    ScriptedRule(match=r"fault|not working|not (been )?switched on|problem|outage|wrong charge|charged twice", structured={"_IntentResult": {"value": "problem_report", "confidence": 0.95, "rationale": "scripted"}}),
    ScriptedRule(match=r"status|my request", structured={"_IntentResult": {"value": "status_query", "confidence": 0.95, "rationale": "scripted"}}),
    ScriptedRule(match=r"\bhello\b|\bhi\b", structured={"_IntentResult": {"value": "smalltalk", "confidence": 0.95, "rationale": "scripted"}}),
]


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "assistant_test.db"
    monkeypatch.setenv("ASSISTANT_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TENANT_CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("TENANT", "netswift")
    monkeypatch.setenv("LLM_PROVIDER", "scripted")
    monkeypatch.setenv("DECISION_SERVICE", "llm_structured")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("MCP_CORE_URL", "http://mcp-core:8000/mcp")
    monkeypatch.setenv("MCP_PAYMENT_URL", "http://mcp-payment:8000/mcp")
    monkeypatch.setenv("MCP_TICKETING_URL", "http://mcp-ticketing:8000/mcp")
    monkeypatch.setenv("MCP_MONITORING_URL", "http://mcp-monitoring:8000/mcp")
    monkeypatch.setenv("MCP_NOTIFICATION_URL", "http://mcp-notification:8000/mcp")

    get_settings.cache_clear()
    get_engine.cache_clear()
    clear_tenant_config_cache()

    import api.main as api_main
    import api.state as state_module

    with TestClient(api_main.app) as test_client:
        # Startup already built a real orchestrator wired to a scripted provider (no
        # fixtures loaded) and a real `ToolGateway` (no live adapters reachable here).
        # Swap in a `FakeGateway` + a `ScriptedProvider` with routing fixtures so chat
        # tests are fully deterministic and offline, exactly like `modes/tests/`.
        fake_gateway = FakeGateway(default_responses(), max_calls_per_turn=14)
        provider = ScriptedProvider(INTENT_RULES)
        decision_service = LLMStructuredDecisionService(provider)
        state_module.state.gateway = fake_gateway
        state_module.state.orchestrator = Orchestrator(
            tenant_config=state_module.state.tenant_config,
            settings=get_settings(),
            provider=provider,
            decision_service=decision_service,
            gateway=fake_gateway,
            audit_log=state_module.state.audit_log,
            session_factory=state_module.state.session_factory,
        )
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    clear_tenant_config_cache()


@pytest.fixture()
def fake_gateway(client):
    from api.state import state

    return state.gateway


def login_as(client, customer_no: str) -> None:
    """Set the session cookie `/api/login` would set (identity comes only from the cookie)."""
    client.cookies.clear()
    client.cookies.set("demo_customer_no", customer_no)

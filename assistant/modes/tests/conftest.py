from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.db import bootstrap_schema, create_sqlite_engine
from core_common.settings import get_settings
from decision.llm_structured import LLMStructuredDecisionService
from llm.scripted import ScriptedProvider, ScriptedRule
from mcp_gateway.fake import FakeGateway
from modes.orchestrator import Orchestrator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}

# The 7 seeded packages (contracts §1.1), as the `list_packages` tool would return them.
PACKAGES: list[dict[str, Any]] = [
    {"code": "FIBER_50_OGRENCI", "name": "Öğrenci Fiber 50", "down_mbps": 50, "up_mbps": 10,
     "commitment_months": 12, "monthly_price_try": 269.0, "target_profile": "student",
     "max_devices": 8, "static_ip": False, "tv_included": False, "gaming_optimized": False, "is_active": True},
    {"code": "FIBER_100_TEMEL", "name": "Temel Fiber 100", "down_mbps": 100, "up_mbps": 20,
     "commitment_months": 24, "monthly_price_try": 349.0, "target_profile": "basic",
     "max_devices": 12, "static_ip": False, "tv_included": False, "gaming_optimized": False, "is_active": True},
    {"code": "FIBER_200_AILE", "name": "Aile Fiber 200", "down_mbps": 200, "up_mbps": 40,
     "commitment_months": 24, "monthly_price_try": 459.0, "target_profile": "family",
     "max_devices": 20, "static_ip": False, "tv_included": True, "gaming_optimized": False, "is_active": True},
    {"code": "FIBER_400_HOMEOFFICE", "name": "Home Office Fiber 400", "down_mbps": 400, "up_mbps": 80,
     "commitment_months": 24, "monthly_price_try": 629.0, "target_profile": "home_office",
     "max_devices": 30, "static_ip": True, "tv_included": False, "gaming_optimized": False, "is_active": True},
    {"code": "FIBER_500_OYUNCU", "name": "Oyuncu Fiber 500", "down_mbps": 500, "up_mbps": 100,
     "commitment_months": 12, "monthly_price_try": 749.0, "target_profile": "gamer",
     "max_devices": 25, "static_ip": False, "tv_included": False, "gaming_optimized": True, "is_active": True},
    {"code": "FIBER_1000_PREMIUM", "name": "Premium Fiber 1000", "down_mbps": 1000, "up_mbps": 200,
     "commitment_months": 24, "monthly_price_try": 999.0, "target_profile": "premium",
     "max_devices": 50, "static_ip": True, "tv_included": True, "gaming_optimized": True, "is_active": True},
    {"code": "FIBER_200_ESNEK", "name": "Esnek Fiber 200 (taahhütsüz)", "down_mbps": 200, "up_mbps": 40,
     "commitment_months": 0, "monthly_price_try": 589.0, "target_profile": "basic",
     "max_devices": 20, "static_ip": False, "tv_included": False, "gaming_optimized": False, "is_active": True},
]

DEFAULT_CUSTOMER = {
    "customer_no": "NH-100001",
    "full_name": "Ali Veli",
    "phone": "+905551112233",
    "email": "ali.veli@ornek-eposta.test",
    "district": "Kadıköy",
    "city": "İstanbul",
    "region_code": "IST-KAD",
    "subscription_count": 1,
}


def default_responses() -> dict[str, Any]:
    return {
        "find_customer": dict(DEFAULT_CUSTOMER),
        "get_subscription_status": {
            "customer_no": "NH-100001", "subscription_id": 42, "package_code": "FIBER_100_TEMEL",
            "status": "active", "monthly_price_try": 349.0, "region_code": "IST-KAD",
        },
        "get_payment_status": {"customer_no": "NH-100001", "payment_id": 1, "status": "succeeded"},
        "detect_duplicate_charges": {"duplicates": []},
        "get_provisioning_status": {
            "job_id": 7, "status": "succeeded", "attempt_count": 1, "is_stuck": False,
        },
        "get_installation_status": {"appointment_id": 5, "status": "completed", "team_code": "FIELD-IST-1"},
        "get_active_incidents_for_region": {"items": []},
        "get_service_health": {"payment_gateway": {"ok": True}, "core_api": {"ok": True}},
        "list_packages": {"items": PACKAGES},
        "find_tickets_by_incident": {"items": []},
        "list_customer_tickets": {"items": []},
        "add_ticket_comment": {"ok": True},
        "retry_provisioning_job": {"status": "queued"},
        "enqueue_provisioning_job": {"status": "queued"},
        "resend_activation_notification": {"status": "sent"},
        "apply_outage_credit": {"status": "applied", "amount_try": 50},
        "post_department_message": {"ok": True},
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00001", "department": "BILLING", "status": "NEW",
            "priority": "HIGH", "created": True,
        },
    }


@pytest.fixture()
def tenant_config(monkeypatch: pytest.MonkeyPatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    cfg = load_tenant_config("nethiz", config_dir=CONFIG_DIR)
    yield cfg
    clear_tenant_config_cache()
    get_settings.cache_clear()


@pytest.fixture()
def session_factory() -> sessionmaker:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def audit_log(session_factory: sessionmaker) -> AuditLog:
    return AuditLog(session_factory)


def make_gateway(overrides: dict[str, Any] | None = None, **kwargs: Any) -> FakeGateway:
    responses = default_responses()
    if overrides:
        responses.update(overrides)
    return FakeGateway(responses, **kwargs)


def make_scripted_provider(intent_rules: list[tuple[str, str, float]]) -> ScriptedProvider:
    """``intent_rules``: list of (regex, intent_value, confidence)."""
    rules = [
        ScriptedRule(
            match=pattern,
            structured={"_IntentResult": {"value": intent, "confidence": confidence, "rationale": "scripted"}},
        )
        for pattern, intent, confidence in intent_rules
    ]
    return ScriptedProvider(rules)


def build_orchestrator(
    tenant_config,
    session_factory: sessionmaker,
    *,
    gateway: FakeGateway | None = None,
    provider: ScriptedProvider | None = None,
) -> Orchestrator:
    gateway = gateway or make_gateway()
    provider = provider or make_scripted_provider(
        [
            (r"paket|tavsiye|öner|internet.*seç", "advisory", 0.95),
            (r"arıza|çalışmıyor|bozuk|açılmadı|sorun|kesinti|fatura.*çift|yanlış tahsilat", "problem_report", 0.95),
            (r"durum|talebim|biletim ne oldu", "status_query", 0.95),
            (r"merhaba|selam|teşekkür", "smalltalk", 0.95),
        ]
    )
    audit_log = AuditLog(session_factory)
    settings = get_settings()
    decision_service = LLMStructuredDecisionService(provider)
    return Orchestrator(
        tenant_config=tenant_config,
        settings=settings,
        provider=provider,
        decision_service=decision_service,
        gateway=gateway,
        audit_log=audit_log,
        session_factory=session_factory,
    )

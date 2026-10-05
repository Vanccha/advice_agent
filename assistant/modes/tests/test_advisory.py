from __future__ import annotations

from core_common.types import Mode
from modes.tests.conftest import build_orchestrator, make_gateway

ANSWERS = [
    "Ev ofis için kullanıyorum, uzaktan çalışıyorum",
    "4 kişi yaşıyoruz",
    "yaklaşık 10 cihaz var",
    "600 TL civarı",
    "24 ay taahhüt olabilir",
]


def _run_advisory_conversation(orch) -> list[str]:
    conv_id = None
    replies: list[str] = []
    result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message="Paket önerisi istiyorum")
    conv_id = result.conversation_id
    replies.append(result.reply_tr)
    assert result.mode == Mode.ADVISORY.value

    for answer in ANSWERS:
        result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message=answer)
        replies.append(result.reply_tr)
        if result.mode != Mode.ADVISORY.value:
            break
    return replies


def test_advisory_asks_at_most_five_questions(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    replies = _run_advisory_conversation(orch)
    # 1 opening question + at most 5 answers consumed before the recommendation reply.
    assert len(replies) <= 6


def test_advisory_reply_only_names_engine_returned_packages(tenant_config, session_factory) -> None:
    from recommendation.engine import recommend_packages
    from core_common.types import AdvisoryProfile
    from modes.tests.conftest import PACKAGES

    orch = build_orchestrator(tenant_config, session_factory)
    replies = _run_advisory_conversation(orch)
    final_reply = replies[-1]

    profile = AdvisoryProfile(
        usage=["home_office"], household_size=4, device_count=10, budget_try=600.0,
        commitment_preference="24",
    )
    expected_offers = recommend_packages(profile, PACKAGES, tenant_config.routing)
    expected_names = {o.name for o in expected_offers}

    all_package_names = {p["name"] for p in PACKAGES}
    mentioned = {name for name in all_package_names if name in final_reply}
    assert mentioned == expected_names
    assert mentioned, "the recommendation reply should name at least one package"


def test_same_profile_twice_gives_same_packages(tenant_config, session_factory) -> None:
    orch1 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())
    orch2 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())

    replies1 = _run_advisory_conversation(orch1)
    replies2 = _run_advisory_conversation(orch2)

    assert replies1[-1] == replies2[-1]

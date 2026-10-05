from __future__ import annotations

from core_common.types import AdvisoryProfile, Mode
from modes.tests.conftest import PACKAGES, build_orchestrator, make_gateway
from recommendation.engine import recommend_packages

# `recommendation.questions.profile_is_complete` (owned by another module, composed as-is
# here) only requires `usage` + one of `household_size`/`device_count` — so a real advisory
# conversation can legitimately finish after 2 answers rather than all 5. These two answers
# are therefore enough to drive it to a recommendation.
FIRST_TWO_ANSWERS = [
    "Ev ofis için kullanıyorum, uzaktan çalışıyorum",
    "4 kişi yaşıyoruz",
]

ALL_FIVE_ANSWERS = FIRST_TWO_ANSWERS + [
    "yaklaşık 10 cihaz var",
    "600 TL civarı",
    "24 ay taahhüt olabilir",
]


def _run_advisory_conversation(orch, answers: list[str]) -> list[str]:
    replies: list[str] = []
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message="Paket önerisi istiyorum")
    conv_id = result.conversation_id
    replies.append(result.reply_tr)
    assert result.mode == Mode.ADVISORY.value

    for answer in answers:
        result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message=answer)
        replies.append(result.reply_tr)
        if result.mode != Mode.ADVISORY.value:
            break
    return replies


def test_advisory_asks_at_most_five_questions(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    # Even when the customer keeps answering past completion, the mode never asks more
    # than max_questions_advisory (5) questions before recommending.
    replies = _run_advisory_conversation(orch, ALL_FIVE_ANSWERS)
    assert len(replies) <= 6


def test_advisory_reply_only_names_engine_returned_packages(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    replies = _run_advisory_conversation(orch, FIRST_TWO_ANSWERS)
    final_reply = replies[-1]

    profile = AdvisoryProfile(usage=["home_office"], household_size=4)
    expected_offers = recommend_packages(profile, PACKAGES, tenant_config.routing)
    expected_names = {o.name for o in expected_offers}

    all_package_names = {p["name"] for p in PACKAGES}
    mentioned = {name for name in all_package_names if name in final_reply}
    assert mentioned == expected_names
    assert mentioned, "the recommendation reply should name at least one package"


def test_same_profile_twice_gives_same_packages(tenant_config, session_factory) -> None:
    orch1 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())
    orch2 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())

    replies1 = _run_advisory_conversation(orch1, FIRST_TWO_ANSWERS)
    replies2 = _run_advisory_conversation(orch2, FIRST_TWO_ANSWERS)

    assert replies1[-1] == replies2[-1]

from __future__ import annotations

from core_common.tr import format_money_try
from core_common.types import AdvisoryProfile, Mode
from llm.scripted import ScriptedProvider, ScriptedRule
from modes.tests.conftest import PACKAGES, build_orchestrator, make_gateway
from recommendation.engine import recommend_packages

# `recommendation.questions.profile_is_complete` now requires usage + a household/device
# count + budget before the profile counts as complete (contracts §4.3: 3-5 questions
# covering usage, household/device count, budget, commitment). The fixed question order
# (usage -> household_size -> device_count -> budget_try -> commitment_preference) means
# four answers are needed even though only one of household_size/device_count is strictly
# required, because device_count is still asked before budget_try regardless.
FIRST_FOUR_ANSWERS = [
    "Ev ofis için kullanıyorum, uzaktan çalışıyorum",  # usage
    "4 kişi yaşıyoruz",  # household_size
    "yaklaşık 10 cihaz var",  # device_count
    "600 TL civarı",  # budget_try -> profile complete here
]

ALL_FIVE_ANSWERS = FIRST_FOUR_ANSWERS + [
    "24 ay taahhüt olabilir",  # commitment_preference (optional, still within the cap)
]

EXPECTED_PROFILE = AdvisoryProfile(
    usage=["home_office"], household_size=4, device_count=10, budget_try=600
)


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


def _advisory_provider_with_reply(reply_text: str | None) -> ScriptedProvider:
    """A scripted provider that routes to ADVISORY like the default test provider, and
    (when `reply_text` is given) answers the advisory verbalization `complete()` call with
    a canned reply. `reply_text=None` leaves that call unscripted, which exercises the
    provider-error fallback path.

    `ScriptedProvider._match` returns the *first* rule whose pattern matches regardless of
    which schema is being requested, so the (very specific) verbalization-reply rule must be
    listed before the (broad, "paket"-matching) intent-classification rule — otherwise the
    intent rule would shadow it and the `complete()` call would look unscripted.
    """
    rules = []
    if reply_text is not None:
        rules.append(ScriptedRule(match=r"Müşteriye anlatılacak paket önerileri", reply=reply_text))
    rules.append(
        ScriptedRule(
            match=r"paket|tavsiye|öner|internet.*seç",
            structured={"_IntentResult": {"value": "advisory", "confidence": 0.95, "rationale": "scripted"}},
        )
    )
    return ScriptedProvider(rules)


def test_advisory_asks_at_most_five_questions(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    # Even when the customer keeps answering past completion, the mode never asks more
    # than max_questions_advisory (5) questions before recommending.
    replies = _run_advisory_conversation(orch, ALL_FIVE_ANSWERS)
    assert len(replies) <= 6


def test_advisory_asks_for_budget_before_recommending(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message="Paket önerisi istiyorum")
    conv_id = result.conversation_id

    for answer in ["Ev ofis için kullanıyorum, uzaktan çalışıyorum", "4 kişi yaşıyoruz", "yaklaşık 10 cihaz var"]:
        result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message=answer)

    # usage + household_size + device_count answered, but budget is still unknown ->
    # must still be asking, not recommending yet.
    assert result.mode == Mode.ADVISORY.value
    assert "bütçe" in result.reply_tr.lower() or "tl" in result.reply_tr.lower()

    result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message="600 TL civarı")
    assert result.mode == Mode.CLOSING.value


def test_advisory_recommends_at_cap_with_note_when_budget_unknown(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message="Paket önerisi istiyorum")
    conv_id = result.conversation_id

    # Explicitly skip budget and commitment -> profile never completes, so the mode must
    # stop at the policy.yaml max_questions_advisory (5) cap and recommend anyway, saying so.
    answers = [
        "Ev ofis için kullanıyorum, uzaktan çalışıyorum",
        "4 kişi yaşıyoruz",
        "yaklaşık 10 cihaz var",
        "geçebilirim",
        "farketmez",
    ]
    for answer in answers:
        result = orch.handle_message(conversation_id=conv_id, customer_no="NH-100001", message=answer)

    assert result.mode == Mode.CLOSING.value
    assert "sorabileceğim soru sayısına ulaştığım" in result.reply_tr.lower()


def test_advisory_reply_only_names_engine_returned_packages(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    replies = _run_advisory_conversation(orch, FIRST_FOUR_ANSWERS)
    final_reply = replies[-1]

    expected_offers = recommend_packages(EXPECTED_PROFILE, PACKAGES, tenant_config.routing)
    expected_names = {o.name for o in expected_offers}

    all_package_names = {p["name"] for p in PACKAGES}
    mentioned = {name for name in all_package_names if name in final_reply}
    assert mentioned == expected_names
    assert mentioned, "the recommendation reply should name at least one package"


def test_same_profile_twice_gives_same_packages(tenant_config, session_factory) -> None:
    orch1 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())
    orch2 = build_orchestrator(tenant_config, session_factory, gateway=make_gateway())

    replies1 = _run_advisory_conversation(orch1, FIRST_FOUR_ANSWERS)
    replies2 = _run_advisory_conversation(orch2, FIRST_FOUR_ANSWERS)

    assert replies1[-1] == replies2[-1]


# -- Change 2: model verbalization of the engine's offers, with validation -----------------


def test_advisory_well_formed_model_reply_is_used_as_is(tenant_config, session_factory) -> None:
    expected_offers = recommend_packages(EXPECTED_PROFILE, PACKAGES, tenant_config.routing)
    best = expected_offers[0]
    reply_text = (
        f"Size en uygun seçenek {best.name}: {best.down_mbps} Mbps hız, "
        f"{format_money_try(best.monthly_price_try)}/ay. Dilerseniz diğer seçenekleri de "
        "birlikte değerlendirebiliriz."
    )
    provider = _advisory_provider_with_reply(reply_text)
    orch = build_orchestrator(tenant_config, session_factory, provider=provider)
    replies = _run_advisory_conversation(orch, FIRST_FOUR_ANSWERS)

    assert replies[-1] == reply_text
    # the offers themselves are still engine-ordered, regardless of the model's wording.
    assert [o.name for o in expected_offers][0] == best.name


def test_advisory_model_reply_with_fabricated_package_is_rejected(tenant_config, session_factory) -> None:
    expected_offers = recommend_packages(EXPECTED_PROFILE, PACKAGES, tenant_config.routing)
    offered_names = {o.name for o in expected_offers}
    fabricated_name = next(p["name"] for p in PACKAGES if p["name"] not in offered_names)

    reply_text = (
        f"{expected_offers[0].name} önerim, ayrıca {fabricated_name} paketini de "
        "düşünebilirsiniz."
    )
    provider_with_fabrication = _advisory_provider_with_reply(reply_text)
    orch_bad = build_orchestrator(tenant_config, session_factory, provider=provider_with_fabrication)
    replies_bad = _run_advisory_conversation(orch_bad, FIRST_FOUR_ANSWERS)

    # baseline: no `complete()` fixture at all -> provider errors -> deterministic template.
    orch_baseline = build_orchestrator(tenant_config, session_factory, provider=_advisory_provider_with_reply(None))
    replies_baseline = _run_advisory_conversation(orch_baseline, FIRST_FOUR_ANSWERS)

    assert fabricated_name not in replies_bad[-1]
    assert replies_bad[-1] == replies_baseline[-1], "a fabricated package must fall back to the template"


def test_advisory_model_reply_with_wrong_price_is_rejected(tenant_config, session_factory) -> None:
    expected_offers = recommend_packages(EXPECTED_PROFILE, PACKAGES, tenant_config.routing)
    best = expected_offers[0]
    wrong_price = format_money_try(best.monthly_price_try + 100)

    reply_text = f"{best.name}: {best.down_mbps} Mbps, {wrong_price}/ay."
    provider_with_wrong_price = _advisory_provider_with_reply(reply_text)
    orch_bad = build_orchestrator(tenant_config, session_factory, provider=provider_with_wrong_price)
    replies_bad = _run_advisory_conversation(orch_bad, FIRST_FOUR_ANSWERS)

    orch_baseline = build_orchestrator(tenant_config, session_factory, provider=_advisory_provider_with_reply(None))
    replies_baseline = _run_advisory_conversation(orch_baseline, FIRST_FOUR_ANSWERS)

    assert wrong_price not in replies_bad[-1]
    assert replies_bad[-1] == replies_baseline[-1], "a wrong price must fall back to the template"

from core_common.types import Department, IssueType, Priority
from tickets.builder import AttemptedStep, StructuredTicket, build_external_ref, build_structured_ticket


def _double_charge_ticket() -> StructuredTicket:
    return build_structured_ticket(
        conversation_id="conv-9f2c1a",
        department=Department.BILLING,
        issue_type=IssueType.DOUBLE_CHARGE,
        priority=Priority.HIGH,
        subject_tr="Çift tahsilat — NH-100042 (2 x 459,00 TRY)",
        body_tr=(
            "Müşteri NH-100042 için 88 ve 89 numaralı ödemelerin aynı gün, aynı tutarda ve "
            "4 dakika arayla başarılı şekilde tahsil edildiği tespit edildi. İade işlemi "
            "yetkim dışında olduğu için gerçekleştirilemedi."
        ),
        requester_customer_no="NH-100042",
        requester_name="Ali Kaya",
        requester_contact="+90 532 111 22 31",
        evidence_record_ids={"subscription_id": 42, "payment_ids": [88, 89]},
        evidence_error_codes=[],
        evidence_observations=["İki ödeme aynı gün, aynı tutar, 4 dakika arayla succeeded."],
        evidence_queried_sources=["diag.payment_status", "payment_api:list_customer_charges"],
        attempted_steps=[
            AttemptedStep(step="get_payment_status", result="2 succeeded charges found", outcome="info"),
            AttemptedStep(
                step="policy_check:issue_refund",
                result="denied: refund_not_permitted",
                outcome="blocked",
            ),
        ],
        suggested_next_step_tr="88 numaralı ödemenin iadesi (459,00 TRY) onaylanmalı.",
        urgency_reason_tr="Müşteriden iki kez tahsilat alındı, yasal süre içinde iade gerekiyor.",
    )


def test_matches_contracts_4_1_shape_field_for_field():
    ticket = _double_charge_ticket()
    payload = ticket.model_dump(mode="json")

    assert payload["department"] == "BILLING"
    assert payload["issue_type"] == "double_charge"
    assert payload["priority"] == "HIGH"
    assert payload["subject"] == "Çift tahsilat — NH-100042 (2 x 459,00 TRY)"
    assert payload["source"] == "api"
    assert payload["external_ref"] == "conv-9f2c1a:double_charge"
    assert payload["incident_ref"] is None
    assert payload["requester"] == {
        "customer_no": "NH-100042",
        "name": "A** K***",
        "contact": "+90 5** *** ** 31",
    }
    assert payload["evidence"]["record_ids"] == {"subscription_id": 42, "payment_ids": [88, 89]}
    assert payload["evidence"]["error_codes"] == []
    assert payload["evidence"]["observations"] == [
        "İki ödeme aynı gün, aynı tutar, 4 dakika arayla succeeded."
    ]
    assert payload["evidence"]["queried_sources"] == [
        "diag.payment_status",
        "payment_api:list_customer_charges",
    ]
    assert payload["attempted_steps"] == [
        {"step": "get_payment_status", "result": "2 succeeded charges found", "outcome": "info"},
        {
            "step": "policy_check:issue_refund",
            "result": "denied: refund_not_permitted",
            "outcome": "blocked",
        },
    ]
    assert payload["affected_customers"] == ["NH-100042"]
    assert payload["suggested_next_step"] == "88 numaralı ödemenin iadesi (459,00 TRY) onaylanmalı."
    assert payload["urgency_reason"] == (
        "Müşteriden iki kez tahsilat alındı, yasal süre içinde iade gerekiyor."
    )


def test_no_raw_pii_survives_but_customer_no_does():
    ticket = _double_charge_ticket()
    payload_text = str(ticket.model_dump(mode="json"))
    assert "+90 532 111 22 31" not in payload_text
    assert "Ali Kaya" not in payload_text
    assert "NH-100042" in payload_text  # allow_unmasked: customer_no survives


def test_external_ref_makes_two_builds_of_the_same_issue_identical():
    ticket1 = _double_charge_ticket()
    ticket2 = _double_charge_ticket()
    assert ticket1.external_ref == ticket2.external_ref == "conv-9f2c1a:double_charge"
    assert ticket1.model_dump(mode="json") == ticket2.model_dump(mode="json")


def test_build_external_ref_is_a_simple_pure_join():
    assert build_external_ref("conv-1", IssueType.STUCK_PROVISIONING) == "conv-1:stuck_provisioning"
    assert build_external_ref("conv-1", "stuck_provisioning") == "conv-1:stuck_provisioning"


def test_affected_customers_defaults_to_the_requester_when_not_given():
    ticket = build_structured_ticket(
        conversation_id="conv-2",
        department=Department.FIELD_INSTALL,
        issue_type=IssueType.MISSED_INSTALLATION,
        priority=Priority.NORMAL,
        subject_tr="Kurulum randevusu kaçırıldı",
        body_tr="Randevu kaçırıldı, saha ekibi bilgilendirildi.",
        requester_customer_no="NH-100099",
        requester_name="Ayşe Demir",
        requester_contact="ayse.demir@ornek-eposta.test",
        suggested_next_step_tr="Yeni randevu planlanmalı.",
        urgency_reason_tr="Müşteri hizmete bağlanamıyor.",
    )
    assert ticket.affected_customers == ["NH-100099"]
    assert "@ornek-eposta.test" not in str(ticket.model_dump(mode="json"))

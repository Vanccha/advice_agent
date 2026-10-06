from core_common.types import Department, IssueType, Priority
from tickets.builder import AttemptedStep, StructuredTicket, build_external_ref, build_structured_ticket


def _double_charge_ticket() -> StructuredTicket:
    return build_structured_ticket(
        conversation_id="conv-9f2c1a",
        department=Department.BILLING,
        issue_type=IssueType.DOUBLE_CHARGE,
        priority=Priority.HIGH,
        subject_en="Double charge — NS-100042 (2 x £45.90)",
        body_en=(
            "For customer NS-100042, payments 88 and 89 were both taken successfully on the "
            "same day, for the same amount, 4 minutes apart. The refund could not be issued "
            "because it is outside my authority."
        ),
        requester_customer_no="NS-100042",
        requester_name="Amy Khan",
        requester_contact="+44 7700 900 131",
        evidence_record_ids={"subscription_id": 42, "payment_ids": [88, 89]},
        evidence_error_codes=[],
        evidence_observations=["Two payments on the same day, same amount, 4 minutes apart, both succeeded."],
        evidence_queried_sources=["diag.payment_status", "payment_api:list_customer_charges"],
        attempted_steps=[
            AttemptedStep(step="get_payment_status", result="2 succeeded charges found", outcome="info"),
            AttemptedStep(
                step="policy_check:issue_refund",
                result="denied: refund_not_permitted",
                outcome="blocked",
            ),
        ],
        suggested_next_step_en="Approve the refund of payment 88 (£45.90).",
        urgency_reason_en="The customer was charged twice; a refund is due within the statutory period.",
    )


def test_matches_contracts_4_1_shape_field_for_field():
    ticket = _double_charge_ticket()
    payload = ticket.model_dump(mode="json")

    assert payload["department"] == "BILLING"
    assert payload["issue_type"] == "double_charge"
    assert payload["priority"] == "HIGH"
    assert payload["subject"] == "Double charge — NS-100042 (2 x £45.90)"
    assert payload["source"] == "api"
    assert payload["external_ref"] == "conv-9f2c1a:double_charge"
    assert payload["incident_ref"] is None
    assert payload["requester"] == {
        "customer_no": "NS-100042",
        "name": "A** K***",
        "contact": "+44 7*** *** *31",
    }
    assert payload["evidence"]["record_ids"] == {"subscription_id": 42, "payment_ids": [88, 89]}
    assert payload["evidence"]["error_codes"] == []
    assert payload["evidence"]["observations"] == [
        "Two payments on the same day, same amount, 4 minutes apart, both succeeded."
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
    assert payload["affected_customers"] == ["NS-100042"]
    assert payload["suggested_next_step"] == "Approve the refund of payment 88 (£45.90)."
    assert payload["urgency_reason"] == (
        "The customer was charged twice; a refund is due within the statutory period."
    )


def test_no_raw_pii_survives_but_customer_no_does():
    ticket = _double_charge_ticket()
    payload_text = str(ticket.model_dump(mode="json"))
    assert "+44 7700 900 131" not in payload_text
    assert "Amy Khan" not in payload_text
    assert "NS-100042" in payload_text  # allow_unmasked: customer_no survives


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
        subject_en="Installation appointment missed",
        body_en="The appointment was missed; the field team has been informed.",
        requester_customer_no="NS-100099",
        requester_name="Grace Evans",
        requester_contact="grace.evans@example-mail.test",
        suggested_next_step_en="Book a new appointment.",
        urgency_reason_en="The customer cannot get connected.",
    )
    assert ticket.affected_customers == ["NS-100099"]
    assert "@example-mail.test" not in str(ticket.model_dump(mode="json"))

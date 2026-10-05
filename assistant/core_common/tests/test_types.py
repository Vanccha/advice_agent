from core_common.types import (
    AdvisoryProfile,
    CommitmentPreference,
    Decision,
    Department,
    Diagnosis,
    DiagnosisScope,
    Intent,
    IssueType,
    Mode,
    PackageOffer,
    PolicyDecision,
    Priority,
    StepType,
    UsageType,
)


def test_issue_type_has_exactly_ten_values():
    assert len(list(IssueType)) == 10


def test_mode_values():
    assert {m.value for m in Mode} == {
        "ROUTER", "ADVISORY", "DIAGNOSTIC", "STATUS_QUERY",
        "ACTION", "AWAITING_APPROVAL", "ESCALATED", "CLOSING",
    }


def test_decision_generic_roundtrip():
    decision: Decision[Intent] = Decision(
        value=Intent.ADVISORY, confidence=0.9, rationale="test", model="scripted", raw={}
    )
    assert decision.value == Intent.ADVISORY
    assert decision.confidence == 0.9


def test_advisory_profile_defaults_and_mutation():
    profile = AdvisoryProfile()
    assert profile.usage == []
    assert profile.household_size is None
    profile.usage = [UsageType.STUDENT]
    profile.commitment_preference = CommitmentPreference.ANY
    assert profile.usage == [UsageType.STUDENT]


def test_package_offer_is_frozen():
    offer = PackageOffer(
        package_code="FIBER_50_OGRENCI", name="Öğrenci Fiber 50",
        down_mbps=50, up_mbps=10, commitment_months=12,
        monthly_price_try=269.0, score=0.8, reasons=["x"], is_best=True,
    )
    try:
        offer.score = 0.1
        assert False, "PackageOffer should be frozen"
    except Exception:
        pass


def test_diagnosis_scope_and_policy_decision():
    diag = Diagnosis(
        root_cause="stuck job", scope=DiagnosisScope.CUSTOMER_SPECIFIC, confidence=0.7
    )
    assert diag.scope == "customer_specific"

    decision = PolicyDecision(allowed=False, escalate_to=Department.BILLING, reason_code="x")
    assert decision.escalate_to == Department.BILLING


def test_step_type_and_priority_values():
    assert StepType.POLICY_CHECK == "policy_check"
    assert Priority.URGENT == "URGENT"

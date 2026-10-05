import pytest

from mcp_gateway.action_runner import UnmappedActionError, make_action_runner
from mcp_gateway.fake import FakeGateway
from mcp_gateway.types import ToolCallOutcome


def test_runner_routes_a_known_action_to_its_tool():
    gateway = FakeGateway({"retry_provisioning_job": {"status": "queued"}})
    runner = make_action_runner(gateway)
    result = runner("retry_provisioning_job", {"job_id": 42})
    assert result["ok"] is True
    assert result["data"] == {"status": "queued"}
    assert gateway.calls_made == [("retry_provisioning_job", {"job_id": 42})]


def test_runner_passes_through_an_adapter_unavailable_outcome_without_raising():
    gateway = FakeGateway({})  # no canned response -> ADAPTER_UNAVAILABLE
    runner = make_action_runner(gateway)
    result = runner("apply_outage_credit", {"subscription_id": 1, "amount_try": 50})
    assert result["ok"] is False
    assert result["error_code"] == "ADAPTER_UNAVAILABLE"


def test_runner_raises_for_an_action_with_no_tool_mapping():
    """An action the adapters cannot perform must fail loudly, never be mis-mapped."""
    gateway = FakeGateway({})
    runner = make_action_runner(gateway)
    with pytest.raises(UnmappedActionError):
        runner("teleport_the_customer", {})


def test_enqueue_provisioning_job_is_mapped():
    """Policy allows it (chaos scenario b), so an adapter tool must back it."""
    gateway = FakeGateway({"enqueue_provisioning_job": {"job_id": 7, "status": "queued"}})
    runner = make_action_runner(gateway)
    result = runner("enqueue_provisioning_job", {"subscription_id": 42})
    assert result["ok"] is True
    assert gateway.calls_made[-1][0] == "enqueue_provisioning_job"


def test_runner_handles_send_department_message():
    gateway = FakeGateway({"post_department_message": {"posted": True}})
    runner = make_action_runner(gateway)
    result = runner("send_department_message", {"channel": "faturalama", "text": "..."})
    assert result["ok"] is True

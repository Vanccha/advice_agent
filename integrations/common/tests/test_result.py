from pydantic import BaseModel

from common.result import ToolResult, fail, ok


class _Data(BaseModel):
    value: int


def test_ok_envelope_shape():
    result = ok(_Data(value=1), source="diag_db")
    assert result.ok is True
    assert result.error is None
    assert result.source == "diag_db"
    assert result.data.value == 1


def test_fail_envelope_preserves_code_and_details():
    result = fail("SCOPE_DENIED", "lacks scope", "core_api", required_scope="billing:refund")
    assert result.ok is False
    assert result.data is None
    assert result.source == "core_api"
    assert result.error.code == "SCOPE_DENIED"
    assert result.error.message == "lacks scope"
    assert result.error.details == {"required_scope": "billing:refund"}


def test_envelope_round_trips_through_json():
    result = ok(_Data(value=42), source="payment_api")
    dumped = result.model_dump(mode="json")
    restored = ToolResult[_Data].model_validate(dumped)
    assert restored.ok is True
    assert restored.data.value == 42
    assert restored.source == "payment_api"


def test_fail_without_source_is_allowed():
    result = fail("INVALID_INPUT", "need at least one filter")
    assert result.source is None
    assert result.error.details == {}

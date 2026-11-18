"""Context handoff (spec §7.6): summary limits, deadline parsing, accepted/completed."""

import pytest

from hermes_internal_bridge.errors import BridgeError
from hermes_internal_bridge.handoff import (
    handoff_result_text,
    handoff_task_text,
    validate_handoff_request,
    validate_handoff_result,
)


def test_valid_request_passes():
    payload = validate_handoff_request({
        "summary": "Починить импорт",
        "constraints": ["не трогать core"],
        "expected_result": "тесты зелёные",
        "deadline_at": "2030-01-01T00:00:00Z",
        "priority": "high",
    }, max_summary_chars=4000)
    assert isinstance(payload["deadline_at"], float)


def test_empty_summary_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_request({"summary": ""}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_summary_over_limit_rejected():
    """§14.1: max_summary_chars limit is enforced."""
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_request({"summary": "д" * 4001}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_bad_constraints_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_request({"summary": "ok", "constraints": [1, 2]}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_bad_priority_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_request({"summary": "ok", "priority": "urgent"}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_unparseable_deadline_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_request({"summary": "ok", "deadline_at": "вчера"}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_epoch_deadline_accepted():
    payload = validate_handoff_request({"summary": "ok", "deadline_at": 1900000000}, max_summary_chars=4000)
    assert payload["deadline_at"] == 1900000000.0


def test_result_validation():
    validate_handoff_result({"status": "completed", "summary": "готово"}, max_summary_chars=4000)
    with pytest.raises(BridgeError) as excinfo:
        validate_handoff_result({"status": "maybe"}, max_summary_chars=4000)
    assert excinfo.value.code == "relay_payload_invalid"


def test_task_text_rendering():
    text = handoff_task_text("Починить импорт", ["не трогать core"], "тесты зелёные", 1900000000)
    assert "Починить импорт" in text
    assert "не трогать core" in text
    assert "Ожидаемый результат" in text
    assert "Срок" in text


def test_result_text_rendering():
    text = handoff_result_text({"status": "completed", "summary": "готово"})
    assert "completed" in text and "готово" in text

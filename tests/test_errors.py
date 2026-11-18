"""Error codes (spec §10)."""

import pytest

from hermes_internal_bridge.errors import DEFAULT_MESSAGES, BridgeError


def test_all_spec_codes_present():
    expected = {
        "relay_route_missing", "route_conflict", "relay_target_unavailable", "relay_hop_limit",
        "relay_duplicate", "relay_filtered_forwarded", "relay_acl_denied", "relay_payload_invalid",
        "relay_payload_too_large", "relay_transcript_failed", "relay_correlation_missing",
        "bus_target_busy", "bus_lease_expired", "bus_dead_letter", "artifact_not_found",
        "artifact_access_denied", "approval_not_supported", "federation_not_supported",
        "gate_degraded",
    }
    assert expected <= set(DEFAULT_MESSAGES)


def test_error_message_names_action_not_paths():
    error = BridgeError("relay_route_missing")
    assert error.code == "relay_route_missing"
    assert "/" not in error.message and "\\" not in error.message


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        BridgeError("not_a_code")

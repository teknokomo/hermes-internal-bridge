"""Artifact references (spec §7.7): links only; base64 and secrets rejected."""

import hashlib

import pytest

from hermes_internal_bridge.artifacts import validate_artifact_ref
from hermes_internal_bridge.errors import BridgeError


def good_ref(**overrides):
    ref = {
        "artifact_id": "a-1",
        "kind": "document",
        "size_bytes": 1200,
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "uri": "/hermes/files/report.pdf",
        "owner_profile": "source-profile",
    }
    ref.update(overrides)
    return ref


def test_valid_ref_passes():
    validate_artifact_ref(good_ref())


def test_document_id_instead_of_uri():
    validate_artifact_ref(good_ref(uri=None, document_id="doc-42"))


def test_base64_payload_rejected():
    """FR-039: bytes never travel in the envelope."""
    for key in ("content_b64", "base64", "data", "content"):
        with pytest.raises(BridgeError) as excinfo:
            validate_artifact_ref(good_ref(**{key: "aGVsbG8="}))
        assert excinfo.value.code == "relay_payload_invalid"


def test_missing_required_field_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref({k: v for k, v in good_ref().items() if k != "sha256"})
    assert excinfo.value.code == "relay_payload_invalid"


def test_both_uri_and_document_id_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref(good_ref(document_id="doc-42"))
    assert excinfo.value.code == "relay_payload_invalid"


def test_bad_sha256_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref(good_ref(sha256="deadbeef"))
    assert excinfo.value.code == "relay_payload_invalid"


def test_negative_size_rejected():
    with pytest.raises(BridgeError):
        validate_artifact_ref(good_ref(size_bytes=-1))


def test_env_reference_rejected_as_acl():
    """FR-041: secrets and .env content can never be artifacts."""
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref(good_ref(uri="/hermes/profiles/x/.env"))
    assert excinfo.value.code == "relay_acl_denied"


def test_auth_json_reference_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref(good_ref(uri="/hermes/auth.json"))
    assert excinfo.value.code == "relay_acl_denied"


def test_secret_looking_uri_rejected():
    with pytest.raises(BridgeError) as excinfo:
        validate_artifact_ref(good_ref(uri="/files/token=sk-abc123DEF456ghi789JKL.bin"))
    assert excinfo.value.code == "relay_acl_denied"

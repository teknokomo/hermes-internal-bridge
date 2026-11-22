"""Envelope validation (spec §9.2, §10): hop/ttl, reserved types, payload limits."""

import pytest

from hermes_internal_bridge.envelope import (
    Envelope,
    new_chat_message_envelope,
    relay_marker,
    strip_relay_marker,
)
from hermes_internal_bridge.errors import BridgeError


def build(**overrides):
    """A valid chat.message envelope with fields overridden for the test."""
    text = overrides.pop("text", "задача для профиля")
    voice = overrides.pop("is_voice_transcript", False)
    envelope = new_chat_message_envelope(
        source=overrides.pop(
            "source",
            {"instance_id": "local", "profile": "source-profile", "platform": "telegram",
             "user_id": "user-1", "chat_id": "dm-chat-1", "thread_id": "", "message_id": "m-1"},
        ),
        destination=overrides.pop(
            "destination",
            {"instance_id": "local", "profile": "target-profile", "chat_id": "topic-1",
             "thread_id": "t-9"},
        ),
        text=text,
        is_voice_transcript=voice,
    )
    for key, value in overrides.items():
        setattr(envelope, key, value)
    return envelope


def test_valid_envelope_passes():
    envelope = build()
    envelope.validate(max_hops=1, max_payload_bytes=65536)
    assert envelope.payload_size() > 0


def test_roundtrip_serialization():
    envelope = build()
    restored = Envelope.from_json(envelope.to_json())
    assert restored.relay_id == envelope.relay_id
    assert restored.correlation_id == envelope.correlation_id
    assert restored.payload == envelope.payload


def test_hop_above_limit_rejected():
    with pytest.raises(BridgeError) as excinfo:
        build(hop=2).validate(max_hops=1)
    assert excinfo.value.code == "relay_hop_limit"


def test_non_local_instance_rejected():
    envelope = build()
    envelope.instance_id = "remote-1"
    with pytest.raises(BridgeError) as excinfo:
        envelope.validate()
    assert excinfo.value.code == "federation_not_supported"


def test_reserved_approval_kind_rejected():
    envelope = build(kind="approval.request")
    with pytest.raises(BridgeError) as excinfo:
        envelope.validate()
    assert excinfo.value.code == "approval_not_supported"


def test_unknown_kind_rejected():
    with pytest.raises(BridgeError) as excinfo:
        build(kind="mystery").validate()
    assert excinfo.value.code == "relay_payload_invalid"


def test_empty_text_rejected():
    envelope = build(text="")
    with pytest.raises(BridgeError) as excinfo:
        envelope.validate()
    assert excinfo.value.code == "relay_payload_invalid"


def test_empty_text_allowed_for_voice_transcript():
    envelope = build(text="", is_voice_transcript=True)
    envelope.validate()


def test_payload_too_large_rejected():
    envelope = build(text="x" * 70000)
    with pytest.raises(BridgeError) as excinfo:
        envelope.validate(max_payload_bytes=65536)
    assert excinfo.value.code == "relay_payload_too_large"
    assert "65536" in excinfo.value.message  # the caller sees the size limit (§10)


def test_ttl_must_be_positive():
    with pytest.raises(BridgeError) as excinfo:
        build(ttl_seconds=0).validate()
    assert excinfo.value.code == "relay_payload_invalid"


def test_relay_marker_roundtrip():
    marker = relay_marker("00000000-0000-4000-8000-000000000001", 1)
    text, relay_id, hop = strip_relay_marker(f"{marker} ответ агента")
    assert relay_id == "00000000-0000-4000-8000-000000000001"
    assert hop == 1
    assert text == "ответ агента"
    assert strip_relay_marker("обычный текст")[1] is None


def test_new_chat_message_envelope_defaults():
    envelope = new_chat_message_envelope(
        source={"profile": "a", "chat_id": "c1"},
        destination={"profile": "b", "chat_id": "c2"},
        text="привет",
    )
    assert envelope.kind == "chat.message"
    assert envelope.hop == 1
    assert envelope.payload["priority"] == "normal"
    assert envelope.payload["is_voice_transcript"] is False

"""Intake (spec §7.1): DM → route → envelope + single ack; voice transcription in source."""

import hashlib
import time

from helpers import FakeEvent, FakeProvider, FakeSource, drain_until, make_components, make_route

from hermes_internal_bridge.errors import BridgeError


def make_intake(tmp_path, *, provider=None, roster=None, **overrides):
    components, acks = make_components(
        tmp_path,
        relay_enabled=True,
        roster=roster or [{"profile": "target-profile", "description": "", "capabilities": []}],
        **overrides,
    )
    components.routes.upsert(make_route("r1"))
    provider = provider or FakeProvider()
    components.intake.transcription_provider = provider
    components.intake.audio_area = tmp_path / "audio"
    return components, acks, provider


def test_routed_dm_creates_envelope_and_single_ack(tmp_path):
    """§8.1: personal chat → profile topic; exactly one message and one ack."""
    components, acks, _ = make_intake(tmp_path)
    event = FakeEvent("сделай отчёт")
    result = components.intake.handle_event(event)
    assert result == {"action": "skip", "reason": "relay_enqueued"}
    rows = components.bus.list_by_status("queued", target_profile="target-profile")
    assert len(rows) == 1
    assert rows[0]["payload_text"] == "сделай отчёт"
    assert rows[0]["route_id"] == "r1"
    assert len(acks) == 1  # FR-004: exactly one short ack
    assert acks[0][1] == "передано"
    assert acks[0][0]["chat_id"] == "dm-chat-1"


def test_relay_disabled_leaves_dispatch_alone(tmp_path):
    components, acks = make_components(tmp_path, relay_enabled=False)
    components.routes.upsert(make_route("r1"))
    components.intake.audio_area = tmp_path / "audio"
    components.intake.transcription_provider = FakeProvider()
    event = FakeEvent("обычный вопрос")
    assert components.intake.handle_event(event) is None
    assert acks == []
    assert components.bus.counts()["queued"] == 0


def test_missing_route_is_fail_closed_but_dispatch_continues(tmp_path):
    """FR-017: no route → relay_route_missing, no nearest-profile guessing."""
    components, acks, _ = make_intake(tmp_path)
    event = FakeEvent("сообщение без маршрута", source=FakeSource(chat_id="unknown-chat"))
    assert components.intake.handle_event(event) is None  # allow → normal dispatch
    assert components.ledger.count_events("relay_route_missing") == 1
    assert components.bus.counts()["queued"] == 0
    assert acks == []


def test_route_conflict_does_not_relay(tmp_path):
    """FR-015: conflict → both routes are not applied, nothing delivered."""
    components, acks, _ = make_intake(tmp_path)
    components.routes.upsert(make_route("r2", mode="mirror"))
    event = FakeEvent("сообщение")
    assert components.intake.handle_event(event) is None
    assert components.ledger.count_events("route_conflict") == 1
    assert components.bus.counts()["queued"] == 0
    assert acks == []


def test_duplicate_relay_id_confirms_without_second_message(tmp_path):
    """FR-011: repeated relay_id → confirmation, no second message."""
    components, acks, _ = make_intake(tmp_path)
    from hermes_internal_bridge.envelope import new_chat_message_envelope

    relay_id = "00000000-0000-4000-8000-0000000000ff"
    envelope = new_chat_message_envelope(
        source={"instance_id": "local", "profile": "default", "platform": "telegram",
                "user_id": "user-1", "chat_id": "dm-chat-1", "thread_id": "", "message_id": "m-1"},
        destination={"instance_id": "local", "profile": "target-profile", "chat_id": "topic-1",
                     "thread_id": "t-9"},
        text="то же сообщение",
        relay_id=relay_id,
    )
    components.bus.enqueue(envelope, route_id="r1")
    queued_before = components.bus.counts()["queued"]
    # feed the same relay_id back through the gate as a re-entering message
    event = FakeEvent(f"[hib:r={relay_id};h=1] то же сообщение")
    result = components.intake.handle_event(event)
    assert result["action"] == "skip"  # gate filters it before intake
    assert components.ledger.count_events("relay_filtered_relay") == 1
    assert components.bus.counts()["queued"] == queued_before  # no second message


def test_oversized_message_rejected_with_error_ack(tmp_path):
    components, acks, _ = make_intake(tmp_path, **{"limits": {"max_payload_bytes": 64}})
    event = FakeEvent("д" * 500)
    result = components.intake.handle_event(event)
    assert result["action"] == "skip"
    assert result["reason"] == "relay_payload_too_large"
    assert len(acks) == 1
    assert "Ошибка" in acks[0][1]
    assert components.bus.counts()["queued"] == 0


def test_voice_message_transcribes_in_source_profile(tmp_path):
    """Source-profile transcription + FR-002: transcript + reference enter the bus, audio never does."""
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake-bytes")
    provider = FakeProvider(text="распознанный текст голосового")
    components, acks, _ = make_intake(tmp_path, provider=provider)
    event = FakeEvent("", message_type="voice", media_urls=[str(audio_path)],
                      media_types=["voice"])
    result = components.intake.handle_event(event)
    assert result["action"] == "skip"
    assert any("голосовое" in text for _, text in acks)
    # the worker enqueues first and ledgers afterwards; wait for BOTH markers so
    # the assertions below cannot race ahead of the worker's ledger write
    ok = drain_until(lambda: components.bus.counts()["queued"] == 1
                     and components.ledger.count_events("relay_transcribed") == 1)
    assert ok, "transcription worker did not finish"
    row = components.bus.list_by_status("queued", target_profile="target-profile")[0]
    envelope = __import__("hermes_internal_bridge.envelope", fromlist=["Envelope"]).Envelope.from_json(
        row["envelope_json"]
    )
    assert envelope.payload["text"] == "распознанный текст голосового"
    assert envelope.payload["is_voice_transcript"] is True
    ref = envelope.payload["attachments"][0]
    assert ref["kind"] == "voice-original"
    assert ref["owner_profile"] == "default"
    assert ref["sha256"] == hashlib.sha256(b"fake-bytes").hexdigest()
    # the copied original stays in the HIB area of the source profile
    assert ref["uri"].startswith(str(tmp_path / "audio"))
    assert provider.calls and provider.calls[0].is_file()
    assert components.ledger.count_events("relay_transcribed") == 1


def test_voice_transcription_failure_sends_no_text(tmp_path):
    """§10: relay_transcript_failed — the message is not delivered."""
    provider = FakeProvider(error=BridgeError("relay_transcript_failed"))
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake")
    components, acks, _ = make_intake(tmp_path, provider=provider)
    event = FakeEvent("", message_type="voice", media_urls=[str(audio_path)], media_types=["voice"])
    assert components.intake.handle_event(event)["action"] == "skip"
    ok = drain_until(lambda: components.ledger.count_events("relay_transcript_failed") == 1)
    assert ok
    assert components.bus.counts()["queued"] == 0
    assert any("Ошибка" in text for _, text in acks)


def test_unexpected_transcription_error_notifies_source(tmp_path):
    """An unexpected provider failure must answer the source instead of leaving it silent."""
    provider = FakeProvider(error=RuntimeError("provider exploded"))
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake")
    components, acks, _ = make_intake(tmp_path, provider=provider)
    event = FakeEvent("", message_type="voice", media_urls=[str(audio_path)], media_types=["voice"])
    result = components.intake.handle_event(event)
    assert result is not None and result["action"] == "skip"
    ok = drain_until(lambda: any("Ошибка" in text for _, text in acks))
    assert ok, "the source was never told that transcription failed"
    assert components.bus.counts()["queued"] == 0
    assert components.ledger.count_events("relay_transcript_failed") == 1


def test_ack_disabled_when_relay_handoff_ack_false(tmp_path):
    components, acks, _ = make_intake(tmp_path, **{"relay": {"handoff_ack": False}})
    components.intake.ack_scheduler = None  # operator turned acks off
    event = FakeEvent("текст")
    assert components.intake.handle_event(event)["action"] == "skip"
    assert components.bus.counts()["queued"] == 1

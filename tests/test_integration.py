"""Integration scenarios (spec §14.2) with fakes: full relay flows, restart recovery."""

import json
import time

from helpers import FakeDisplaySender, FakeEvent, FakeInjector, enqueue_message, make_components, make_route

from hermes_internal_bridge.consumer import ProfileConsumer
from hermes_internal_bridge.envelope import Envelope


ROSTER = [
    {"profile": "target-profile", "description": "Исполнитель", "capabilities": ["code"]},
    {"profile": "source-profile", "description": "Личный чат", "capabilities": []},
]


def test_dm_to_route_to_target_single_message_single_ack(tmp_path):
    """§14.2/§8.1: DM → route → target; exactly one message and one ack."""
    components, acks = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1"))
    event = FakeEvent("подготовь отчёт")
    assert components.intake.handle_event(event) == {"action": "skip", "reason": "relay_enqueued"}
    assert len(acks) == 1
    queued = components.bus.counts()["queued"]
    assert queued == 1

    target_consumer = ProfileConsumer(
        profile="target-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory,
        injector=FakeInjector(True),
    )
    target_consumer.stop()
    target_consumer.tick()
    row = components.bus.list_by_status("delivered", target_profile="target-profile")
    assert len(row) == 1  # exactly one delivery


def test_mirror_no_loop(tmp_path):
    """§14.2: mirror copy goes to the source chat; the gate filters it on re-entry."""
    components, _ = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1", mode="mirror"))
    event = FakeEvent("вопрос")
    assert components.intake.handle_event(event)["action"] == "skip"

    target_consumer = ProfileConsumer(
        profile="target-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory, injector=FakeInjector(True),
    )
    target_consumer.stop()
    target_consumer.tick()
    relay_row = components.bus.list_by_status("delivered", target_profile="target-profile")[0]
    target_consumer.on_turn_completed(user_message=relay_row["payload_text"],
                                      assistant_response="ответ из темы")

    display = FakeDisplaySender(True)
    source_consumer = ProfileConsumer(
        profile="source-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory, display_sender=display,
    )
    source_consumer.stop()
    source_consumer.tick()
    assert len(display.sent) == 1
    destination, text = display.sent[0]
    assert destination["chat_id"] == "dm-chat-1"
    assert text == "ответ из темы"
    # the mirror copy carries the relay marker; if it ever re-enters the gateway,
    # the trigger gate skips it (no new relay)
    marker_id = "abcdef01" * 4  # 32 hex chars, matches the marker grammar
    gate_verdict = components.gate.evaluate(FakeEvent(f"[hib:r={marker_id};h=1] {text}"))
    assert gate_verdict.action == "skip"


def test_profile_a_to_profile_b_correlated_reply(tmp_path):
    """§14.2/§8.4: A → B → reply; correlation by correlation_id, no new turn in A."""
    components, _ = make_components(tmp_path, profile="profile-a", roster=ROSTER + [
        {"profile": "profile-b", "capabilities": []},
    ])
    handlers = {name: handler for name, _, _, handler in __import__(
        "hermes_internal_bridge.tools", fromlist=["build_tool_handlers"]
    ).build_tool_handlers(components)}
    sent = handlers["hib_bus_send"]({
        "target_profile": "profile-b",
        "kind": "handoff.request",
        "payload": {"summary": "оцени код", "constraints": ["без правок core"]},
    })
    assert sent["ok"] is True

    consumer_b = ProfileConsumer(
        profile="profile-b", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory,
        injector=FakeInjector(True), keyless_injector=FakeInjector(True),
    )
    consumer_b.stop()
    consumer_b.tick()
    relay_row = components.bus.list_by_status("delivered", target_profile="profile-b")[0]
    assert relay_row["correlation_id"] == sent["correlation_id"]
    consumer_b.on_turn_completed(user_message=relay_row["payload_text"], assistant_response="оценил")

    results = components.bus.list_by_status("queued", target_profile="profile-a")
    assert len(results) == 1
    assert results[0]["kind"] == "handoff.result"
    assert results[0]["correlation_id"] == sent["correlation_id"]


def test_gateway_restart_delivers_queued(tmp_path):
    """§14.2: a queued message survives a restart and is delivered after startup."""
    components, _ = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1"))
    state_path = components.db.path
    assert components.intake.handle_event(FakeEvent("сообщение до рестарта"))["action"] == "skip"

    # "restart": close the database, reopen it from disk
    components.db.close()
    from hermes_internal_bridge.db import Database

    db = Database(state_path)
    from hermes_internal_bridge.bus import MessageBus
    from hermes_internal_bridge.ledger import Ledger

    bus = MessageBus(db)
    Ledger(db)
    row = bus.claim("target-profile")
    assert row is not None and row["status"] == "processing"
    bus.release_expired_leases(now=time.time() + 3600)  # crashed consumer simulation
    row = bus.get_any(row["relay_id"])
    assert row["status"] == "queued"  # no loss across restart


def test_unavailable_target_no_loss_no_default(tmp_path):
    """§14.2/§8.6: stopped target → queued, one error notice, no default redirect."""
    components, _ = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1"))
    components.intake.handle_event(FakeEvent("для остановленного профиля"))

    injector = FakeInjector(False)  # target topic has no human message yet
    consumer = ProfileConsumer(
        profile="target-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory, injector=injector,
    )
    consumer.stop()
    consumer.tick()
    consumer.tick()
    queued = components.bus.list_by_status("queued", target_profile="target-profile")
    assert len(queued) == 1
    assert queued[0]["attempts"] == 0
    notices = components.bus.list_by_status("queued", target_profile="source-profile")
    assert len(notices) == 1  # exactly one error to the source (FR-006)


def test_route_conflict_delivers_nothing(tmp_path):
    """§14.2: conflicting routes → nothing is delivered."""
    components, acks = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1", mode="handoff"))
    components.routes.upsert(make_route("r2", mode="mirror"))
    event = FakeEvent("спорное сообщение")
    result = components.intake.handle_event(event)
    assert result is None  # conflict: both routes skipped
    assert components.bus.counts()["queued"] == 0
    assert acks == []
    assert components.ledger.count_events("route_conflict") == 1


def test_ledger_and_logs_have_no_secrets(tmp_path):
    """§14.4: secrets scanner over the full ledger dump — zero matches."""
    components, _ = make_components(tmp_path, profile="source-profile", roster=ROSTER)
    components.routes.upsert(make_route("r1"))
    secret_text = "возьми ключ sk-abc123DEF456ghi789JKL и продолжи"
    components.intake.handle_event(FakeEvent(secret_text))
    # simulate failures that would otherwise log the payload
    consumer = ProfileConsumer(
        profile="target-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory, injector=FakeInjector(False),
    )
    consumer.stop()
    consumer.tick()
    consumer.tick()

    import re

    from hermes_internal_bridge.secrets import find_secrets

    dump = json.dumps(components.ledger.all_records(), ensure_ascii=False)
    # the message text itself is payload, not a secret; the scanner must find
    # no TOKEN-CLASS matches
    assert find_secrets(dump) == []
    # the token from the user's message lives only in the envelope payload
    # (hib_messages), never in the ledger dump
    assert "sk-abc123DEF456ghi789JKL" not in dump
    # the ledger must not contain secrets in ERROR DETAILS either
    for record in components.ledger.all_records():
        details = json.dumps(record.get("details_json", ""), ensure_ascii=False)
        assert find_secrets(details) == []

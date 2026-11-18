"""Consumer (variant B): injection, availability (Omisa #2), mirror, handoff result."""

import time

from helpers import enqueue_message, make_components, make_route

from hermes_internal_bridge.consumer import ProfileConsumer
from hermes_internal_bridge.envelope import new_chat_message_envelope


class FakeInjector:
    """Scriptable ctx.inject_message stand-in."""

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def __call__(self, origin=None, content="", **kwargs):
        self.calls.append({"origin": origin, "content": content, **kwargs})
        return self.result


class FakeDisplaySender:
    def __init__(self, result=True):
        self.result = result
        self.sent = []

    def __call__(self, destination, text):
        self.sent.append((dict(destination), text))
        return self.result


def make_consumer(components, *, injector=None, display=None):
    consumer = components.consumer
    consumer.injector = injector if injector is not None else FakeInjector(True)
    consumer.keyless_injector = FakeInjector(True)
    consumer.display_sender = display if display is not None else FakeDisplaySender(True)
    consumer.stop()  # run ticks manually
    return consumer


def test_inject_true_marks_delivered(tmp_path):
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components)
    injector = FakeInjector(True)
    consumer.injector = injector
    envelope = enqueue_message(components.bus)
    consumer.tick()
    row = components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "delivered"
    assert "[hib:r=" in injector.calls[0]["content"]
    assert injector.calls[0]["origin"]["chat_id"] == "topic-1"
    assert components.ledger.count_events("relay_delivered") == 1


def test_inject_false_keeps_message_queued_without_loss(tmp_path):
    """Omisa addition #2: inject_message False → relay_target_unavailable, stays queued."""
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components, injector=FakeInjector(False))
    envelope = enqueue_message(components.bus)
    consumer.tick()
    row = components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "queued"
    assert row["attempts"] == 0  # availability failure does not consume attempts
    assert row["source_notified"] == 1  # source told exactly once
    assert components.ledger.count_events("relay_target_unavailable") == 1
    # the notice goes to the source profile as a display message
    notices = components.bus.list_by_status("queued", target_profile="source-profile")
    assert len(notices) == 1
    assert notices[0]["kind"] == "health"


def test_inject_false_notice_sent_only_once(tmp_path):
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components, injector=FakeInjector(False))
    enqueue_message(components.bus)
    consumer.tick()
    consumer.tick()
    consumer.tick()
    assert len(components.bus.list_by_status("queued", target_profile="source-profile")) == 1


def test_unknown_target_profile_blocks_route(tmp_path):
    """FR-024: unknown profile → blocked, no delivery, no default fallback."""
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "someone-else"}])
    consumer = make_consumer(components, injector=FakeInjector(True))
    envelope = enqueue_message(components.bus)  # targeted at target-profile (not in roster)
    consumer.tick()
    row = components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert components.ledger.count_events("relay_target_unavailable") == 1


def test_consumer_cannot_touch_other_profile_messages(tmp_path):
    """Security: scoping by target_profile (Omisa constraint)."""
    components, _ = make_components(tmp_path, profile="profile-a",
                                    roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components, injector=FakeInjector(True))
    message = enqueue_message(components.bus, destination={
        "instance_id": "local", "profile": "profile-b", "chat_id": "c",
    })
    consumer.tick()
    row = components.bus.get(message.relay_id, target_profile="profile-b")
    assert row is not None and row["status"] == "queued"  # untouched by profile-a
    assert components.bus.claim("profile-b") is not None  # only profile-b consumes it
    assert components.bus.get(message.relay_id, target_profile="profile-a") is None


def test_turn_completion_marks_done(tmp_path):
    """FR-012: the reply is correlated by correlation_id; completed_by recorded."""
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components)
    envelope = enqueue_message(components.bus)
    consumer.tick()
    assert components.bus.get(envelope.relay_id, target_profile="target-profile")["status"] == "delivered"
    consumer.on_turn_completed(user_message=f"[hib:r={envelope.relay_id};h=1] {envelope.payload_text()}",
                               assistant_response="готово")
    row = components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "done"
    assert components.ledger.count_events("relay_done") == 1


def test_mirror_route_copies_reply_to_source(tmp_path):
    """FR-005: mirror mode → the reply is enqueued for the source chat, no new relay loop."""
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    components.routes.upsert(make_route("r1", mode="mirror"))
    consumer = make_consumer(components)
    envelope = enqueue_message(components.bus, route_id="r1")
    consumer.tick()
    consumer.on_turn_completed(user_message=f"[hib:r={envelope.relay_id};h=1] {envelope.payload_text()}",
                               assistant_response="вот ответ")
    mirrors = components.bus.list_by_status("queued", target_profile="source-profile")
    assert len(mirrors) == 1
    assert mirrors[0]["relay_mirror"] == 1
    assert mirrors[0]["payload_text"] == "вот ответ"
    assert mirrors[0]["correlation_id"] == envelope.correlation_id


def test_handoff_mode_returns_result(tmp_path):
    """FR-037: handoff.result carries status and a short summary."""
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    consumer = make_consumer(components)
    from hermes_internal_bridge.envelope import new_relay_id

    relay_id = new_relay_id()
    envelope = new_chat_message_envelope(
        relay_id=relay_id,
        text="",
        source={"instance_id": "local", "profile": "source-profile", "platform": "telegram",
                "user_id": "user-1", "chat_id": "dm-chat-1", "thread_id": "", "message_id": ""},
        destination={"instance_id": "local", "profile": "target-profile", "chat_id": "topic-1",
                     "thread_id": ""},
        payload={"summary": "починить X", "constraints": [], "expected_result": "работает",
                 "priority": "normal"},
    )
    envelope.kind = "handoff.request"
    components.bus.enqueue(envelope)
    consumer.tick()
    row = components.bus.get(relay_id, target_profile="target-profile")
    assert row["status"] == "delivered"
    # accepted_by recorded (FR-036)
    handoff = components.db.query_one("SELECT * FROM hib_handoffs WHERE relay_id=?", (relay_id,))
    assert handoff["accepted_by"] == "target-profile"
    consumer.on_turn_completed(user_message=row["payload_text"], assistant_response="сделано")
    assert components.bus.get(relay_id, target_profile="target-profile")["status"] == "done"
    handoff = components.db.query_one("SELECT * FROM hib_handoffs WHERE relay_id=?", (relay_id,))
    assert handoff["completed_by"] == "target-profile"
    results = components.bus.list_by_status("queued", target_profile="source-profile")
    assert len(results) == 1
    assert results[0]["kind"] == "handoff.result"


def test_display_delivery_sends_mirror_to_source_chat(tmp_path):
    components, _ = make_components(tmp_path, profile="target-profile", roster=[{"profile": "target-profile"}])
    components.routes.upsert(make_route("r1", mode="mirror"))
    target_consumer = make_consumer(components)
    envelope = enqueue_message(components.bus, route_id="r1")
    target_consumer.tick()
    target_consumer.on_turn_completed(user_message=f"[hib:r={envelope.relay_id};h=1] {envelope.payload_text()}",
                                      assistant_response="ответ в личку")
    # the SOURCE profile's consumer (owning the DM adapter) delivers the mirror
    display = FakeDisplaySender(True)
    from hermes_internal_bridge.consumer import ProfileConsumer

    source_consumer = ProfileConsumer(
        profile="source-profile", bus=components.bus, ledger=components.ledger,
        routes=components.routes, directory=components.directory, display_sender=display,
    )
    source_consumer.stop()
    source_consumer.tick()
    assert len(display.sent) == 1
    destination, text = display.sent[0]
    assert destination["chat_id"] == "dm-chat-1"
    assert text == "ответ в личку"
    mirror_id = components.bus.list_by_status("done", target_profile="source-profile")[0]["relay_id"]
    assert mirror_id  # completed


def test_processing_error_counts_attempts_towards_dead(tmp_path):
    components, _ = make_components(tmp_path, profile="target-profile",
                                    roster=[{"profile": "target-profile"}],
                                    **{"bus": {"backoff_seconds": [0, 0, 0, 0]}})
    components.bus.max_attempts = 2
    consumer = make_consumer(components)
    consumer.injector = ExplodingInjector()
    envelope = enqueue_message(components.bus)
    consumer.tick()
    consumer.tick()
    row = components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "dead"
    assert components.ledger.count_events("bus_dead_letter") == 1


class ExplodingInjector:
    def __call__(self, origin=None, content="", **kwargs):
        raise RuntimeError("session exploded")

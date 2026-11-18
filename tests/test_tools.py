"""Plugin tools (FR-047..FR-049): scoping, limits, error codes instead of tracebacks."""

import pytest

from helpers import enqueue_message, make_components, make_route

from hermes_internal_bridge.tools import build_tool_handlers


@pytest.fixture()
def tools(tmp_path):
    components, _ = make_components(tmp_path, profile="target-profile",
                                    roster=[
                                        {"profile": "target-profile", "description": "self",
                                         "capabilities": ["code"]},
                                        {"profile": "other-profile", "description": "",
                                         "capabilities": []},
                                    ])
    handlers = {name: handler for name, _, _, handler in build_tool_handlers(components)}
    return components, handlers


def test_hib_status_shape(tools):
    components, handlers = tools
    result = handlers["hib_status"]({})
    assert result["ok"] is True
    assert result["queues"]["queued"] == 0
    assert result["profile"] == "target-profile"
    assert "filtered_forwarded" in result


def test_hib_routes_list(tools):
    components, handlers = tools
    components.routes.upsert(make_route("r1"))
    result = handlers["hib_routes_list"]({})
    assert result["ok"] is True
    assert result["routes"][0]["route_id"] == "r1"
    assert result["routes"][0]["specificity"] > 0


def test_hib_relay_send_requires_known_profile(tools):
    """FR-024 via tools: unknown target → relay_route_missing, no default fallback."""
    _, handlers = tools
    result = handlers["hib_relay_send"]({"target_profile": "ghost", "text": "привет"})
    assert result["ok"] is False
    assert result["error"] == "relay_route_missing"


def test_hib_relay_send_empty_text_rejected(tools):
    _, handlers = tools
    result = handlers["hib_relay_send"]({"target_profile": "other-profile", "text": "  "})
    assert result["ok"] is False
    assert result["error"] == "relay_payload_invalid"


def test_hib_relay_send_enqueues_with_correlation(tools):
    components, handlers = tools
    result = handlers["hib_relay_send"]({"target_profile": "other-profile", "text": "задача",
                                         "chat_id": "topic-2"})
    assert result["ok"] is True
    row = components.bus.get(result["relay_id"], target_profile="other-profile")
    assert row is not None
    assert row["correlation_id"] == result["correlation_id"]
    assert components.ledger.count_events("relay_enqueued") == 1


def test_hib_bus_send_handoff_validates_summary(tools):
    _, handlers = tools
    result = handlers["hib_bus_send"]({"target_profile": "other-profile",
                                       "kind": "handoff.request", "payload": {"summary": ""}})
    assert result["ok"] is False
    assert result["error"] == "relay_payload_invalid"


def test_hib_bus_send_handoff_ok(tools):
    components, handlers = tools
    result = handlers["hib_bus_send"]({"target_profile": "other-profile",
                                       "kind": "handoff.request",
                                       "payload": {"summary": "починить", "constraints": ["без core"]}})
    assert result["ok"] is True
    handoff = components.db.query_one("SELECT * FROM hib_handoffs WHERE relay_id=?",
                                      (result["relay_id"],))
    assert handoff["summary"] == "починить"


def test_hib_bus_send_rejects_base64_artifact(tools):
    _, handlers = tools
    result = handlers["hib_bus_send"]({
        "target_profile": "other-profile", "kind": "artifact.ready",
        "payload": {"attachments": [{"artifact_id": "a", "kind": "doc", "size_bytes": 1,
                                     "sha256": "0" * 64, "owner_profile": "x",
                                     "uri": "/f", "content_b64": "aGVsbG8="}]},
    })
    assert result["ok"] is False
    assert result["error"] == "relay_payload_invalid"


def test_hib_bus_read_scoped_to_own_profile(tools):
    """FR-048: a tool cannot read another profile's messages."""
    components, handlers = tools
    enqueue_message(components.bus, text="чужое", destination={
        "instance_id": "local", "profile": "other-profile", "chat_id": "c",
    })
    enqueue_message(components.bus, text="своё")
    result = handlers["hib_bus_read"]({"status": "queued"})
    texts = [m["payload_text"] for m in result["messages"]]
    assert texts == ["своё"]


def test_hib_bus_ack_only_own_messages(tools):
    components, handlers = tools
    foreign = enqueue_message(components.bus, destination={
        "instance_id": "local", "profile": "other-profile", "chat_id": "c",
    })
    result = handlers["hib_bus_ack"]({"relay_id": foreign.relay_id, "status": "done"})
    assert result["ok"] is False


def test_hib_bus_ack_done(tools):
    components, handlers = tools
    envelope = enqueue_message(components.bus)
    handlers["hib_bus_read"]({})  # smoke
    result = handlers["hib_bus_ack"]({"relay_id": envelope.relay_id, "status": "done"})
    assert result["ok"] is True
    assert result["status"] == "done"


def test_hib_directory_lists_roster(tools):
    _, handlers = tools
    result = handlers["hib_directory"]({})
    profiles = {p["profile"] for p in result["profiles"]}
    assert profiles == {"target-profile", "other-profile"}


def test_handler_never_leaks_traceback(tools):
    """FR-049: errors return codes, not tracebacks."""
    components, handlers = tools
    components.routes.list = None  # break it on purpose
    result = handlers["hib_routes_list"]({})
    assert result["ok"] is False
    assert "Traceback" not in str(result)

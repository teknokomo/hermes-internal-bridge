"""Route registry (spec §7.3): specificity, conflicts, fail-closed resolution."""

import pytest

from helpers import make_route

from hermes_internal_bridge.db import Database
from hermes_internal_bridge.errors import BridgeError
from hermes_internal_bridge.routes import RouteRegistry


@pytest.fixture()
def registry(tmp_path):
    db = Database(tmp_path / "hib.sqlite3")
    yield RouteRegistry(db)
    db.close()


def test_upsert_and_list(registry):
    registry.upsert(make_route("r1"))
    registry.upsert(make_route("r2", source={"platform": "telegram"}, enabled=False))
    items = registry.list()
    assert len(items) == 2


def test_specificity_weights():
    full = make_route("full", source={"platform": "telegram", "user_id": "u", "chat_id": "c", "thread_id": "t"})
    assert full.specificity() == 16 + 8 + 4 + 1
    chat_only = make_route("chat", source={"platform": "telegram", "chat_id": "c"})
    assert chat_only.specificity() == 1 + 4
    user_only = make_route("user", source={"user_id": "u"})
    assert user_only.specificity() == 16


def test_conjunctive_matching():
    route = make_route("r1", source={"platform": "telegram", "chat_id": "dm-1"})
    assert route.matches({"platform": "telegram", "chat_id": "dm-1", "user_id": "u"})
    assert not route.matches({"platform": "max", "chat_id": "dm-1"})
    assert not route.matches({"platform": "telegram", "chat_id": "other"})


def test_route_missing_is_fail_closed(registry):
    decision = registry.resolve({"platform": "telegram", "chat_id": "unknown"})
    assert decision.route is None
    assert decision.error is not None
    assert decision.error.code == "relay_route_missing"


def test_equal_specificity_conflict(registry):
    """FR-015: two enabled routes of equal specificity — both are not applied."""
    registry.upsert(make_route("r1", mode="handoff"))
    registry.upsert(make_route("r2", mode="mirror"))
    decision = registry.resolve({"platform": "telegram", "user_id": "user-1", "chat_id": "dm-chat-1"})
    assert decision.error is not None
    assert decision.error.code == "route_conflict"
    assert set(decision.conflict_route_ids) == {"r1", "r2"}


def test_disabled_route_does_not_conflict(registry):
    registry.upsert(make_route("r1", mode="handoff"))
    registry.upsert(make_route("r2", mode="mirror", enabled=False))
    decision = registry.resolve({"platform": "telegram", "user_id": "user-1", "chat_id": "dm-chat-1"})
    assert decision.error is None
    assert decision.route.route_id == "r1"


def test_higher_specificity_wins(registry):
    registry.upsert(make_route("specific", source={"platform": "telegram", "user_id": "user-1", "chat_id": "dm-chat-1"}))
    registry.upsert(make_route("broad", source={"platform": "telegram"}))
    decision = registry.resolve({"platform": "telegram", "user_id": "user-1", "chat_id": "dm-chat-1"})
    assert decision.route.route_id == "specific"
    assert decision.route.specificity() == 1 + 16 + 4


def test_disable_and_enable(registry):
    registry.upsert(make_route("r1", enabled=False))
    assert registry.set_enabled("r1", True) is True
    assert registry.get("r1").enabled is True
    assert registry.set_enabled("missing", True) is False


def test_target_requires_chat(registry):
    route = make_route("nochat", target={"profile": "target-profile"})
    with pytest.raises(BridgeError) as excinfo:
        registry.upsert(route)
    assert excinfo.value.code == "relay_payload_invalid"


def test_route_cannot_declare_unknown_fields(registry):
    route = make_route("bad", source={"platform": "telegram", "group_id": "x"})
    with pytest.raises(BridgeError) as excinfo:
        registry.upsert(route)
    assert excinfo.value.code == "relay_payload_invalid"


def test_delete(registry):
    registry.upsert(make_route("r1"))
    assert registry.delete("r1") is True
    assert registry.get("r1") is None

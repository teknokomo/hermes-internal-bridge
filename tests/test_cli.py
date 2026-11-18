"""Operator CLI (FR-046): status, routes, relay show/replay, peers, directory."""

import json

import pytest

from helpers import enqueue_message, make_components, make_route

from hermes_internal_bridge import cli


@pytest.fixture()
def cli_components(tmp_path, monkeypatch):
    components, _ = make_components(tmp_path, profile="target-profile",
                                    roster=[{"profile": "target-profile", "capabilities": ["code"]}])
    components.routes.upsert(make_route("r1"))
    state = str(components.db.path)
    from hermes_internal_bridge.bus import MessageBus
    from hermes_internal_bridge.db import Database
    from hermes_internal_bridge.directory import Directory
    from hermes_internal_bridge.ledger import Ledger
    from hermes_internal_bridge.routes import RouteRegistry

    roster = [{"profile": "target-profile", "capabilities": ["code"]}]

    def fake_open(config_overrides=None):
        db = Database(state)
        return (db, Ledger(db), RouteRegistry(db), MessageBus(db),
                Directory(db, roster=roster))

    monkeypatch.setattr(cli, "_open_components", fake_open)
    return components


def test_status_prints_queues_and_routes(cli_components, capsys):
    enqueue_message(cli_components.bus)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "queued=1" in out
    assert "r1" in out


def test_routes_list_and_enable(cli_components, capsys):
    assert cli.main(["routes", "list"]) == 0
    assert "r1" in capsys.readouterr().out
    assert cli.main(["routes", "disable", "r1"]) == 0
    assert cli_components.routes.get("r1").enabled is False
    assert cli.main(["routes", "enable", "r1"]) == 0
    assert cli_components.routes.get("r1").enabled is True


def test_routes_add_from_file(cli_components, capsys, tmp_path):
    route_file = tmp_path / "route.json"
    route_file.write_text(json.dumps({
        "route_id": "r2",
        "source": {"platform": "max", "chat_id": "max-1"},
        "target": {"profile": "other", "chat_id": "t-2"},
        "mode": "mirror",
        "priority": 5,
        "enabled": False,
    }), encoding="utf-8")
    assert cli.main(["routes", "add", "--file", str(route_file)]) == 0
    route = cli_components.routes.get("r2")
    assert route is not None and route.mode == "mirror"
    assert route.enabled is False  # enabling stays an explicit operator step


def test_routes_add_rejects_invalid_route(cli_components, capsys, tmp_path):
    route_file = tmp_path / "bad.json"
    route_file.write_text(json.dumps({
        "route_id": "bad", "source": {"platform": "max"},
        "target": {"profile": "other"},  # no chat_id
        "mode": "handoff",
    }), encoding="utf-8")
    assert cli.main(["routes", "add", "--file", str(route_file)]) == 1


def test_relay_show_and_replay(cli_components, capsys):
    envelope = enqueue_message(cli_components.bus)
    assert cli.main(["relay", "show", envelope.relay_id]) == 0
    out = capsys.readouterr().out
    assert envelope.relay_id in out
    cli_components.bus.set_status(envelope.relay_id, "failed",
                                  target_profile="target-profile", error="x")
    assert cli.main(["relay", "replay", envelope.relay_id]) == 0
    row = cli_components.bus.get(envelope.relay_id, target_profile="target-profile")
    assert row["status"] == "queued"
    assert cli_components.ledger.count_events("replay") == 1  # operator actions are ledgered


def test_relay_show_missing_exits_nonzero(cli_components):
    assert cli.main(["relay", "show", "no-such"]) == 1


def test_peers_is_federation_stub(cli_components, capsys):
    assert cli.main(["peers"]) == 1
    assert "federation_not_supported" in capsys.readouterr().out


def test_directory_prints_roster(cli_components, capsys):
    assert cli.main(["directory"]) == 0
    assert "target-profile" in capsys.readouterr().out

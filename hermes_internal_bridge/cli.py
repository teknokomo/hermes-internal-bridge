"""Operator CLI (spec FR-046): ``hib status/routes/relay/replay/peers/directory``.

Operator scope: route management, replay, and dead-letter handling are
operator-only actions and always ledgered (FR-018, FR-031, FR-032).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import paths
from .bus import MessageBus
from .db import Database
from .directory import Directory
from .ledger import Ledger
from .routes import Route, RouteRegistry
from .errors import BridgeError


def _open_components(config_overrides: dict[str, Any] | None = None):
    """Open the shared database and build the operator-scope components."""
    from .plugin import CONFIG_DEFAULTS

    config = {name: dict(section) for name, section in CONFIG_DEFAULTS.items()}
    config.update(config_overrides or {})
    state_value = str(config["ledger"].get("state_path") or "").strip()
    state_path = paths.expand_path(state_value) if state_value else paths.default_bus_state_path()
    if not Path(state_path).exists():
        raise BridgeError("relay_payload_invalid",
                          f"База шины не найдена: {state_path}. Плагин ещё не запускался в каком-либо профиле?")
    db = Database(state_path)
    ledger = Ledger(db)
    routes = RouteRegistry(db)
    bus = MessageBus(
        db,
        lease_seconds=int(config["bus"]["lease_seconds"]),
        max_attempts=int(config["bus"]["max_attempts"]),
        backoff_seconds=list(config["bus"]["backoff_seconds"]),
        availability_poll_seconds=int(config["bus"]["availability_poll_seconds"]),
    )
    directory = Directory(db, roster=list(config["directory"].get("profiles") or []),
                          refresh_seconds=int(config["directory"]["refresh_seconds"]))
    return db, ledger, routes, bus, directory


def cmd_status(args) -> int:
    db, ledger, routes, bus, directory = _open_components()
    try:
        counts = bus.counts()
        enabled = routes.enabled_routes()
        print("HIB status")
        print(f"  state: {db.path}")
        print(f"  queues: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
        print(f"  routes: {len(enabled)} active / {len(routes.list())} total")
        for route in enabled:
            print(f"    - {route.route_id}: {json.dumps(route.source, ensure_ascii=False)} → "
                  f"{json.dumps(route.target, ensure_ascii=False)} [{route.mode}]")
        print(f"  filtered forwarded: {ledger.count_events('relay_filtered_forwarded')}")
        print(f"  filtered relay: {ledger.count_events('relay_filtered_relay')}")
        print(f"  gate degraded: {ledger.count_events('gate_degraded')}")
        for event in ledger.recent(limit=5):
            details = event.get("details") or {}
            print(f"  [{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event['created_at']))}] "
                  f"{event['event']} relay={event['relay_id'][:12]} {json.dumps(details, ensure_ascii=False)[:160]}")
    finally:
        db.close()
    return 0


def cmd_routes(args) -> int:
    db, ledger, routes, bus, directory = _open_components()
    try:
        if args.action == "list":
            items = routes.list()
            if not items:
                print("Маршрутов нет. Добавьте через 'hib routes add --file <json>'.")
            for route in items:
                state = "enabled" if route.enabled else "disabled"
                print(f"{route.route_id} [{state}] mode={route.mode} priority={route.priority} "
                      f"specificity={route.specificity()}")
                print(f"  source: {json.dumps(route.source, ensure_ascii=False)}")
                print(f"  target: {json.dumps(route.target, ensure_ascii=False)}")
            return 0
        if args.action in ("enable", "disable"):
            ok = routes.set_enabled(args.route_id, args.action == "enable")
            if not ok:
                print(f"Маршрут {args.route_id} не найден.", file=sys.stderr)
                return 1
            ledger.event("", "route_" + args.action, details={"route_id": args.route_id})
            print(f"Маршрут {args.route_id}: {args.action}d.")
            return 0
        if args.action == "add":
            document = json.loads(Path(args.file).read_text(encoding="utf-8"))
            entries = document if isinstance(document, list) else [document]
            for item in entries:
                route = Route(
                    route_id=str(item["route_id"]),
                    source=dict(item.get("source") or {}),
                    target=dict(item.get("target") or {}),
                    mode=str(item.get("mode", "handoff")),
                    priority=int(item.get("priority", 0)),
                    enabled=bool(item.get("enabled", False)),
                )
                routes.upsert(route)
                ledger.event("", "route_add", details={"route_id": route.route_id})
            print(f"Добавлено маршрутов: {len(entries)} (включение — 'hib routes enable <id>').")
            return 0
        if args.action == "remove":
            ok = routes.delete(args.route_id)
            if not ok:
                print(f"Маршрут {args.route_id} не найден.", file=sys.stderr)
                return 1
            ledger.event("", "route_remove", details={"route_id": args.route_id})
            print(f"Маршрут {args.route_id} удалён.")
            return 0
    finally:
        db.close()
    return 1


def cmd_relay(args) -> int:
    db, ledger, routes, bus, directory = _open_components()
    try:
        if args.action == "show":
            row = bus.get_any(args.relay_id)
            if row is None:
                print(f"Сообщение {args.relay_id} не найдено.", file=sys.stderr)
                return 1
            row.pop("envelope_json", None)
            print(json.dumps(row, ensure_ascii=False, indent=2))
            print("ledger:")
            for event in ledger.recent(relay_id=args.relay_id, limit=20):
                print(f"  {event['event']} {json.dumps(event.get('details') or {}, ensure_ascii=False)[:200]}")
            return 0
        if args.action == "replay":
            row = bus.get_any(args.relay_id)
            if row is None:
                print(f"Сообщение {args.relay_id} не найдено.", file=sys.stderr)
                return 1
            bus.set_status(args.relay_id, "queued", target_profile=row["target_profile"], error="")
            ledger.event(args.relay_id, "replay", details={"target_profile": row["target_profile"], "by": "operator"})
            print(f"Сообщение {args.relay_id} возвращено в очередь (target={row['target_profile']}).")
            return 0
    finally:
        db.close()
    return 1


def cmd_peers(args) -> int:
    # FR-051 / §5.3: federation arrives in 0.3.0; the command is a stub.
    print("peers: federation_not_supported — межинстансная федерация появится в 0.3.0.")
    return 1


def cmd_directory(args) -> int:
    db, ledger, routes, bus, directory = _open_components()
    try:
        entries = directory.entries(bus_counts=_processing_by_profile(bus))
        if not entries:
            print("Реестр пуст: заполните directory.profiles в настройках плагина.")
            return 0
        for entry in entries:
            capabilities = ",".join(entry.capabilities) or "-"
            print(f"{entry.profile} [{entry.status}] capabilities={capabilities} {entry.description}")
    finally:
        db.close()
    return 0


def _processing_by_profile(bus: MessageBus) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in bus.list_by_status("processing", target_profile=None, limit=500):
        counts.setdefault(row["target_profile"], {"processing": 0})
        counts[row["target_profile"]]["processing"] += 1
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hib", description="Hermes Internal Bridge — операторский CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="очереди, маршруты, последние ошибки")

    routes_parser = sub.add_parser("routes", help="управление маршрутами (оператор)")
    routes_sub = routes_parser.add_subparsers(dest="action", required=True)
    routes_sub.add_parser("list", help="список маршрутов")
    enable = routes_sub.add_parser("enable", help="включить маршрут")
    enable.add_argument("route_id")
    disable = routes_sub.add_parser("disable", help="выключить маршрут")
    disable.add_argument("route_id")
    add = routes_sub.add_parser("add", help="добавить/обновить маршрут из JSON-файла")
    add.add_argument("--file", required=True)
    remove = routes_sub.add_parser("remove", help="удалить маршрут")
    remove.add_argument("route_id")

    relay_parser = sub.add_parser("relay", help="инспекция и replay сообщений")
    relay_sub = relay_parser.add_subparsers(dest="action", required=True)
    show = relay_sub.add_parser("show", help="показать сообщение и его ledger")
    show.add_argument("relay_id")
    replay = relay_sub.add_parser("replay", help="вернуть сообщение в очередь (оператор)")
    replay.add_argument("relay_id")

    sub.add_parser("peers", help="заготовка федерации 0.3.0")
    sub.add_parser("directory", help="каталог профилей")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "status": cmd_status,
        "routes": cmd_routes,
        "relay": cmd_relay,
        "peers": cmd_peers,
        "directory": cmd_directory,
    }
    handler = commands.get(args.command)
    if handler is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        return handler(args)
    except BridgeError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

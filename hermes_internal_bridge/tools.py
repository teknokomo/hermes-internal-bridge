"""Plugin tools (spec §7.9): hib_* handlers, scoped to the host profile (FR-048).

Handlers never bypass the ACL, route scope, or trigger gate. They return spec
§10 error codes, never tracebacks (FR-049). Read/send operations are always
scoped to the profile hosting the tool.
"""

from __future__ import annotations

from typing import Any, Callable

from .envelope import Envelope, new_chat_message_envelope, new_correlation_id, new_relay_id
from .errors import BridgeError
from .schemas import TOOL_SPEC_TABLE


def build_tool_handlers(components) -> list[tuple[str, dict, str, Callable]]:
    """``(name, schema, description, handler)`` tuples, handlers pre-scoped to *components*."""
    result = []
    for name, schema, description in TOOL_SPEC_TABLE:
        result.append((name, schema, description, _scoped(_HANDLERS[name], components)))
    return result


def _scoped(handler: Callable, components) -> Callable:
    def call(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return handler(components, args or {})
        except BridgeError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}
        except Exception as exc:  # never leak tracebacks to the model (FR-049)
            return {"ok": False, "error": "relay_payload_invalid", "message": str(exc)[:200]}

    return call


def _tool_status(components, args: dict[str, Any]) -> dict[str, Any]:
    counts = components.bus.counts()
    routes = components.routes.list()
    recent_errors = [
        {"event": e["event"], "relay_id": e["relay_id"], "details": e.get("details", {})}
        for e in components.ledger.recent(limit=10)
        if str(e.get("event", "")).startswith(("relay_", "bus_", "gate_"))
    ]
    return {
        "ok": True,
        "profile": components.profile,
        "relay_enabled": bool(components.config["relay"]["enabled"]),
        "queues": counts,
        "active_routes": sum(1 for r in routes if r.enabled),
        "routes_total": len(routes),
        "filtered_forwarded": components.ledger.count_events("relay_filtered_forwarded"),
        "filtered_relay": components.ledger.count_events("relay_filtered_relay"),
        "gate_degraded": components.ledger.count_events("gate_degraded"),
        "dead_letter": counts.get("dead", 0),
        "recent_errors": recent_errors[:5],
    }


def _tool_routes_list(components, args: dict[str, Any]) -> dict[str, Any]:
    routes = components.routes.list()
    return {
        "ok": True,
        "routes": [
            {
                "route_id": r.route_id,
                "enabled": r.enabled,
                "source": r.source,
                "target": r.target,
                "mode": r.mode,
                "priority": r.priority,
                "specificity": r.specificity(),
            }
            for r in routes
        ],
    }


def _tool_relay_send(components, args: dict[str, Any]) -> dict[str, Any]:
    """Policy-checked relay of one chat message to a target profile (§8.4 flow)."""
    text = str(args.get("text", "")).strip()
    if not text:
        raise BridgeError("relay_payload_invalid", "Пустой текст сообщения.")
    target_profile = str(args.get("target_profile", "")).strip()
    if not target_profile:
        raise BridgeError("relay_payload_invalid", "target_profile обязателен.")
    if components.directory is not None and not components.directory.known(target_profile):
        # FR-024: no delivery and no default-profile fallback.
        raise BridgeError("relay_route_missing", "Целевой профиль отсутствует в реестре.")
    target_chat = str(args.get("chat_id", "") or "")
    target_thread = str(args.get("thread_id", "") or "")
    # FR-025: a tool call may not invent a destination; exactly an enabled route authorizes it.
    allowed = any(
        route.enabled
        and str(route.target.get("profile", "")) == target_profile
        and str(route.target.get("chat_id", "") or "") == target_chat
        and str(route.target.get("thread_id", "") or "") == target_thread
        for route in components.routes.list()
    )
    if not allowed:
        raise BridgeError("relay_route_missing", "Цель не разрешена ни одним включённым маршрутом.")
    correlation_id = str(args.get("correlation_id", "") or "").strip() or new_correlation_id()
    envelope = new_chat_message_envelope(
        source={
            "instance_id": "local",
            "profile": components.profile,
            "platform": str(args.get("platform", "") or ""),
            "user_id": str(args.get("user_id", "") or ""),
            "chat_id": str(args.get("source_chat_id", "") or ""),
            "thread_id": "",
            "message_id": "",
        },
        destination={
            "instance_id": "local",
            "profile": target_profile,
            "chat_id": str(args.get("chat_id", "") or ""),
            "thread_id": str(args.get("thread_id", "") or ""),
        },
        text=text,
        correlation_id=correlation_id,
    )
    envelope.validate(
        max_hops=int(components.config["relay"]["max_hops"]),
        max_payload_bytes=int(components.config["limits"]["max_payload_bytes"]),
    )
    components.bus.enqueue(envelope)
    components.ledger.event(envelope.relay_id, "relay_enqueued", details={
        "via": "tool", "target_profile": target_profile, "correlation_id": correlation_id,
    })
    return {"ok": True, "relay_id": envelope.relay_id, "correlation_id": correlation_id}


def _tool_bus_send(components, args: dict[str, Any]) -> dict[str, Any]:
    """Generic bus envelope: handoff.request/task.request/artifact.ready (§8.5)."""
    kind = str(args.get("kind", "")).strip()
    payload = args.get("payload")
    target_profile = str(args.get("target_profile", "")).strip()
    if kind not in ("handoff.request", "task.request", "task.result", "artifact.ready", "health"):
        raise BridgeError("relay_payload_invalid", "Недопустимый тип конверта для шины.")
    if not target_profile:
        raise BridgeError("relay_payload_invalid", "target_profile обязателен.")
    if not isinstance(payload, dict):
        raise BridgeError("relay_payload_invalid", "payload должен быть объектом.")
    if kind == "handoff.request":
        from .handoff import validate_handoff_request

        payload = validate_handoff_request(
            payload, max_summary_chars=int(components.config["limits"]["max_summary_chars"])
        )
    envelope = Envelope(
        relay_id=str(args.get("relay_id", "") or "").strip() or new_relay_id(),
        correlation_id=str(args.get("correlation_id", "") or "").strip() or new_correlation_id(),
        kind=kind,
        source={
            "instance_id": "local",
            "profile": components.profile,
            "platform": "",
            "user_id": "",
            "chat_id": "",
            "thread_id": "",
            "message_id": "",
        },
        destination={
            "instance_id": "local",
            "profile": target_profile,
            "chat_id": str(args.get("chat_id", "") or ""),
            "thread_id": str(args.get("thread_id", "") or ""),
        },
        payload=dict(payload),
    )
    envelope.validate(
        max_hops=int(components.config["relay"]["max_hops"]),
        max_payload_bytes=int(components.config["limits"]["max_payload_bytes"]),
    )
    components.bus.enqueue(envelope)
    components.ledger.event(envelope.relay_id, "relay_enqueued", details={
        "via": "tool", "kind": kind, "target_profile": target_profile,
    })
    return {"ok": True, "relay_id": envelope.relay_id, "correlation_id": envelope.correlation_id}


def _tool_bus_read(components, args: dict[str, Any]) -> dict[str, Any]:
    """Read messages addressed to THIS profile only (FR-048: no cross-profile reads)."""
    status = str(args.get("status", "queued")).strip()
    try:
        limit = min(int(args.get("limit", 10) or 10), 50)
    except (TypeError, ValueError):
        limit = 10
    rows = components.bus.list_by_status(status, target_profile=components.profile, limit=limit)
    return {
        "ok": True,
        "profile": components.profile,
        "messages": [
            {
                "relay_id": row["relay_id"],
                "correlation_id": row["correlation_id"],
                "kind": row["kind"],
                "status": row["status"],
                "attempts": row["attempts"],
                "source_profile": row["source_profile"],
                "payload_text": str(row["payload_text"])[:2000],
            }
            for row in rows
        ],
    }


def _tool_bus_ack(components, args: dict[str, Any]) -> dict[str, Any]:
    relay_id = str(args.get("relay_id", "")).strip()
    if not relay_id:
        raise BridgeError("relay_payload_invalid", "relay_id обязателен.")
    status = str(args.get("status", "done")).strip()
    if status not in ("done", "failed"):
        raise BridgeError("relay_payload_invalid", "status должен быть done|failed.")
    row = components.bus.get(relay_id, target_profile=components.profile)
    if row is None:
        raise BridgeError("relay_payload_invalid", "Сообщение не найдено в этом профиле.")
    if status == "done":
        components.bus.complete(relay_id, target_profile=components.profile, completed_by=components.profile)
        components.ledger.event(relay_id, "relay_done", details={
            "completed_by": components.profile, "via": "tool",
        })
    else:
        components.bus.fail(
            relay_id, target_profile=components.profile,
            error=str(args.get("error", "") or "failed via tool"),
        )
        components.ledger.event(relay_id, "relay_failed", details={"via": "tool"})
    updated = components.bus.get(relay_id, target_profile=components.profile)
    return {"ok": True, "relay_id": relay_id, "status": updated["status"] if updated else "unknown"}


def _tool_directory(components, args: dict[str, Any]) -> dict[str, Any]:
    entries = components.directory.entries(bus_counts=_per_profile_processing(components))
    return {
        "ok": True,
        "profiles": [
            {
                "profile": e.profile,
                "description": e.description,
                "capabilities": e.capabilities,
                "status": e.status,
            }
            for e in entries
        ],
    }


def _per_profile_processing(components) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in components.bus.list_by_status("processing", target_profile=None, limit=500):
        counts.setdefault(row["target_profile"], {"processing": 0})
        counts[row["target_profile"]]["processing"] += 1
    return counts


_HANDLERS: dict[str, Callable] = {
    "hib_status": _tool_status,
    "hib_routes_list": _tool_routes_list,
    "hib_relay_send": _tool_relay_send,
    "hib_bus_send": _tool_bus_send,
    "hib_bus_read": _tool_bus_read,
    "hib_bus_ack": _tool_bus_ack,
    "hib_directory": _tool_directory,
}

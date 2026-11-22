"""Route registry and deterministic policy (spec §7.3, §6.3).

One route — one destination, no fan-out. Matching is conjunctive over the
declared source fields; the highest additive specificity wins, mirroring the
core gateway weights (user_id 16, thread_id 8, chat_id 4, platform 1).
Two enabled routes of equal specificity matching the same source chat are a
``route_conflict`` and are both not applied (FR-015) — no priority tie-break
can rescue them; ``priority`` is recorded for audit only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .errors import BridgeError

SPECIFICITY_WEIGHTS: dict[str, int] = {
    "user_id": 16,
    "thread_id": 8,
    "chat_id": 4,
    "platform": 1,
}

MODES: tuple[str, ...] = ("handoff", "mirror")


@dataclass
class Route:
    """A single operator-defined route (FR-014)."""

    route_id: str
    source: dict[str, Any]
    target: dict[str, Any]
    mode: str = "handoff"
    priority: int = 0
    enabled: bool = False

    def to_rows(self) -> tuple[str, str, int, int]:
        return (
            json.dumps(self.source, ensure_ascii=False, sort_keys=True),
            json.dumps(self.target, ensure_ascii=False, sort_keys=True),
            1 if self.enabled else 0,
            int(self.priority),
        )

    @classmethod
    def from_row(cls, row) -> "Route":
        return cls(
            route_id=row["route_id"],
            source=json.loads(row["source_json"]),
            target=json.loads(row["target_json"]),
            mode=row["mode"],
            priority=row["priority"],
            enabled=bool(row["enabled"]),
        )

    def validate(self) -> None:
        if not self.route_id or not self.route_id.strip():
            raise BridgeError("relay_payload_invalid", "route_id не может быть пустым.")
        if self.mode not in MODES:
            raise BridgeError("relay_payload_invalid", "mode должен быть handoff или mirror.")
        if not isinstance(self.source, dict) or not isinstance(self.target, dict):
            raise BridgeError("relay_payload_invalid", "source и target должны быть объектами.")
        if not str(self.target.get("profile", "")).strip():
            raise BridgeError("relay_payload_invalid", "target.profile обязателен.")
        if not str(self.target.get("chat_id", "")).strip():
            # FR-003: delivery goes to exactly one (profile, chat, thread) target.
            raise BridgeError("relay_payload_invalid", "target.chat_id обязателен.")
        for key in self.source:
            if key not in ("platform", "user_id", "chat_id", "thread_id"):
                raise BridgeError("relay_payload_invalid", f"Неизвестное поле source.{key}.")
        for key in self.target:
            if key not in ("profile", "chat_id", "thread_id"):
                raise BridgeError("relay_payload_invalid", f"Неизвестное поле target.{key}.")

    def specificity(self) -> int:
        """Additive specificity of the declared source fields."""
        weight = 0
        for key in ("platform", "chat_id", "thread_id", "user_id"):
            if str(self.source.get(key, "") or "").strip():
                weight += SPECIFICITY_WEIGHTS[key]
        return weight

    def matches(self, source: dict[str, Any]) -> bool:
        """True when every declared source field matches the incoming source (conjunctive)."""
        for key in ("platform", "user_id", "chat_id", "thread_id"):
            declared = str(self.source.get(key, "") or "").strip()
            if not declared:
                continue
            if str(source.get(key, "") or "") != declared:
                return False
        return True


@dataclass
class RouteDecision:
    """The outcome of deterministic route resolution (FR-019 audit trail)."""

    route: Route | None
    specificity: int = 0
    conflict_route_ids: tuple[str, ...] = ()
    error: BridgeError | None = None
    candidates: tuple[Route, ...] = field(default_factory=tuple)


class RouteRegistry:
    """Operator-managed route table in the shared database (FR-018)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------- CRUD

    def upsert(self, route: Route) -> None:
        route.validate()
        now = time.time()
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO hib_routes
                    (route_id, enabled, source_json, target_json, mode, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    source_json=excluded.source_json,
                    target_json=excluded.target_json,
                    mode=excluded.mode,
                    priority=excluded.priority,
                    updated_at=excluded.updated_at
                """,
                (
                    route.route_id,
                    1 if route.enabled else 0,
                    *route.to_rows()[:2],
                    route.mode,
                    route.priority,
                    now,
                    now,
                ),
            )

    def set_enabled(self, route_id: str, enabled: bool) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE hib_routes SET enabled=?, updated_at=? WHERE route_id=?",
                (1 if enabled else 0, time.time(), route_id),
            )
            return cursor.rowcount > 0

    def delete(self, route_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM hib_routes WHERE route_id=?", (route_id,))
            return cursor.rowcount > 0

    def list(self) -> list[Route]:
        rows = self._db.query("SELECT * FROM hib_routes ORDER BY priority DESC, route_id")
        return [Route.from_row(row) for row in rows]

    def get(self, route_id: str) -> Route | None:
        row = self._db.query_one("SELECT * FROM hib_routes WHERE route_id=?", (route_id,))
        return Route.from_row(row) if row else None

    def enabled_routes(self) -> list[Route]:
        return [route for route in self.list() if route.enabled]

    # -------------------------------------------------------------- resolution

    def resolve(self, source: dict[str, Any]) -> RouteDecision:
        """Deterministically pick the destination for *source* (FR-017, FR-015)."""
        candidates = [route for route in self.enabled_routes() if route.matches(source)]
        if not candidates:
            return RouteDecision(route=None, error=BridgeError("relay_route_missing"))
        best_weight = max(route.specificity() for route in candidates)
        best = [route for route in candidates if route.specificity() == best_weight]
        if len(best) > 1:
            # FR-015: equal specificity for one source chat — both are not applied.
            return RouteDecision(
                route=None,
                specificity=best_weight,
                conflict_route_ids=tuple(route.route_id for route in best),
                error=BridgeError("route_conflict"),
                candidates=tuple(candidates),
            )
        winner = best[0]
        return RouteDecision(
            route=winner,
            specificity=best_weight,
            candidates=tuple(candidates),
        )

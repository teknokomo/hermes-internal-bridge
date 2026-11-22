"""Audit ledger (spec §7.8): every delivery event, filtered for secrets before the write."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .db import Database
from .secrets import scrub


class Ledger:
    """Append-only audit trail; never stores secrets (FR-042, FR-043)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def event(
        self,
        relay_id: str,
        event: str,
        *,
        route_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> str:
        details = scrub(details or {})
        record_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO hib_ledger (id, relay_id, event, route_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record_id, relay_id, event, route_id, json.dumps(details, ensure_ascii=False), time.time()),
        )
        return record_id

    def recent(self, *, limit: int = 50, relay_id: str | None = None) -> list[dict[str, Any]]:
        if relay_id:
            rows = self._db.query(
                "SELECT * FROM hib_ledger WHERE relay_id=? ORDER BY created_at DESC LIMIT ?",
                (relay_id, limit),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM hib_ledger ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except Exception:
                item["details"] = {}
            result.append(item)
        return result

    def count_events(self, event: str) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM hib_ledger WHERE event=?", (event,))
        return int(row["n"]) if row else 0

    def all_records(self, *, limit: int = 100000) -> list[dict[str, Any]]:
        rows = self._db.query("SELECT * FROM hib_ledger ORDER BY created_at LIMIT ?", (limit,))
        return [dict(row) for row in rows]

"""Local profile bus (spec §7.5): a durable at-most-once transport.

States: queued → delivered → processing → done | failed | dead. Availability
failures (target profile or chat unreachable) do not consume attempts and never
dead-letter (FR-006: no loss); processing failures consume attempts and reach
the dead-letter after ``max_attempts`` (FR-031). Every operation is scoped by
``target_profile`` — a consumer for profile A can neither see nor mutate
messages addressed to profile B (covered by a dedicated test).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .db import Database, row_to_dict
from .envelope import Envelope
from .errors import BridgeError

STATES: tuple[str, ...] = ("queued", "delivered", "processing", "done", "failed", "dead")

# Kinds bound for an agent turn in the target profile vs display-only copies.
AGENT_KINDS: frozenset[str] = frozenset({"chat.message", "handoff.request", "task.request"})
DISPLAY_KINDS: frozenset[str] = frozenset({"handoff.result", "artifact.ready", "health"})


def is_display_message(envelope: Envelope) -> bool:
    """Display messages go to a chat via the profile adapter; agent messages are injected."""
    if envelope.relay_mirror:
        return True
    return envelope.kind in DISPLAY_KINDS


class MessageBus:
    """Queue operations on the shared database."""

    def __init__(self, db: Database, *, lease_seconds: int = 300, max_attempts: int = 5,
                 backoff_seconds: list[int] | None = None,
                 availability_poll_seconds: int = 60) -> None:
        self._db = db
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = list(backoff_seconds or [5, 30, 120, 600])
        self.availability_poll_seconds = availability_poll_seconds

    # ---------------------------------------------------------------- intake

    def enqueue(self, envelope: Envelope, *, route_id: str = "") -> str:
        """Insert a message; a repeated ``relay_id`` is a no-op confirmation (FR-011)."""
        now = time.time()
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO hib_messages
                        (relay_id, correlation_id, source_profile, target_profile, envelope_json,
                         status, attempts, created_at, updated_at, kind, payload_text, payload_size,
                         route_id, relay_mirror)
                    VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.relay_id,
                        envelope.correlation_id,
                        envelope.source_profile,
                        envelope.target_profile,
                        envelope.to_json(),
                        now,
                        now,
                        envelope.kind,
                        envelope.payload_text(),
                        envelope.payload_size(),
                        route_id,
                        1 if envelope.relay_mirror else 0,
                    ),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                # Duplicate relay_id: the first message stands, callers treat this as confirmation.
                raise BridgeError("relay_duplicate") from exc
            raise
        if envelope.kind == "handoff.request":
            self._insert_handoff(envelope, now)
        return envelope.relay_id

    def _insert_handoff(self, envelope: Envelope, now: float) -> None:
        payload = envelope.payload
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise BridgeError("relay_payload_invalid", "handoff.request требует непустой summary.")
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO hib_handoffs
                    (relay_id, summary, constraints_json, expected_result, deadline_at, priority,
                     accepted_by, completed_by)
                VALUES (?, ?, ?, ?, ?, ?, '', '')
                ON CONFLICT(relay_id) DO NOTHING
                """,
                (
                    envelope.relay_id,
                    summary,
                    json.dumps(payload.get("constraints") or [], ensure_ascii=False),
                    str(payload.get("expected_result") or ""),
                    payload.get("deadline_at") if isinstance(payload.get("deadline_at"), (int, float)) else None,
                    str(payload.get("priority") or "normal"),
                ),
            )

    # --------------------------------------------------------------- consume

    def claim(self, target_profile: str, *, now: float | None = None) -> dict[str, Any] | None:
        """Lease the oldest deliverable message for *target_profile* (FR-028, FR-029)."""
        now = time.time() if now is None else now
        with self._db.transaction() as conn:
            self._release_expired_leases(conn, now)
            row = conn.execute(
                """
                SELECT relay_id FROM hib_messages
                WHERE target_profile=? AND status='queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND relay_mirror=0
                  AND kind IN ('chat.message','handoff.request','task.request')
                ORDER BY created_at LIMIT 1
                """,
                (target_profile, now),
            ).fetchone()
            if row is None:
                return None
            relay_id = row["relay_id"]
            conn.execute(
                """
                UPDATE hib_messages
                SET status='processing', lease_until=?, updated_at=?
                WHERE relay_id=? AND target_profile=? AND status='queued'
                """,
                (now + self.lease_seconds, now, relay_id, target_profile),
            )
        return self.get(relay_id, target_profile=target_profile)

    def _release_expired_leases(self, conn, now: float) -> int:
        """Return expired leases to the queue and grow the attempt counter (FR-029)."""
        expired = conn.execute(
            "SELECT relay_id, attempts FROM hib_messages"
            " WHERE status='processing' AND lease_until IS NOT NULL AND lease_until < ?",
            (now,),
        ).fetchall()
        for row in expired:
            attempts = int(row["attempts"]) + 1
            conn.execute(
                "UPDATE hib_messages SET status='queued', lease_until=NULL, attempts=?,"
                " last_error='bus_lease_expired', next_attempt_at=?, updated_at=? WHERE relay_id=?",
                (attempts, now + self._backoff_delay(attempts), now, row["relay_id"]),
            )
        return len(expired)

    def release_expired_leases(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._db.transaction() as conn:
            return self._release_expired_leases(conn, now)

    def claim_display(self, target_profile: str, *, now: float | None = None) -> dict[str, Any] | None:
        """Lease the oldest display-only message for *target_profile* (mirror copies, notices)."""
        now = time.time() if now is None else now
        with self._db.transaction() as conn:
            self._release_expired_leases(conn, now)
            row = conn.execute(
                """
                SELECT relay_id FROM hib_messages
                WHERE target_profile=? AND status='queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND (relay_mirror=1 OR kind IN ('handoff.result','artifact.ready','health'))
                ORDER BY created_at LIMIT 1
                """,
                (target_profile, now),
            ).fetchone()
            if row is None:
                return None
            relay_id = row["relay_id"]
            conn.execute(
                """
                UPDATE hib_messages
                SET status='processing', lease_until=?, updated_at=?
                WHERE relay_id=? AND target_profile=? AND status='queued'
                """,
                (now + self.lease_seconds, now, relay_id, target_profile),
            )
        return self.get(relay_id, target_profile=target_profile)

    def get(self, relay_id: str, *, target_profile: str) -> dict[str, Any] | None:
        """Fetch one message; the profile scope is mandatory and enforced."""
        row = self._db.query_one(
            "SELECT * FROM hib_messages WHERE relay_id=? AND target_profile=?",
            (relay_id, target_profile),
        )
        return row_to_dict(row)

    def get_any(self, relay_id: str) -> dict[str, Any] | None:
        """Operator-scope fetch (CLI): no profile scoping."""
        row = self._db.query_one("SELECT * FROM hib_messages WHERE relay_id=?", (relay_id,))
        return row_to_dict(row)

    def set_payload_text(self, relay_id: str, *, target_profile: str, text: str) -> None:
        """Record the exact injected text so reply correlation matches ``post_llm_call``."""
        self._db.execute(
            "UPDATE hib_messages SET payload_text=?, updated_at=? WHERE relay_id=? AND target_profile=?",
            (text, time.time(), relay_id, target_profile),
        )

    def mark_accepted(self, relay_id: str, *, target_profile: str, accepted_by: str) -> None:
        """Record ``accepted_by`` on a handoff request (FR-036)."""
        self._db.execute(
            "UPDATE hib_handoffs SET accepted_by=? WHERE relay_id=?",
            (accepted_by, relay_id),
        )

    # ------------------------------------------------------------ transitions

    def mark_delivered(self, relay_id: str, *, target_profile: str) -> bool:
        """processing → delivered: the injection was accepted by the host."""
        self._db.execute(
            "UPDATE hib_messages SET status='delivered', lease_until=NULL, last_error='', updated_at=?"
            " WHERE relay_id=? AND target_profile=? AND status='processing'",
            (time.time(), relay_id, target_profile),
        )
        row = self.get(relay_id, target_profile=target_profile)
        return bool(row and row["status"] == "delivered")

    def complete(self, relay_id: str, *, target_profile: str, completed_by: str = "") -> bool:
        now = time.time()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE hib_messages SET status='done', lease_until=NULL, updated_at=?
                WHERE relay_id=? AND target_profile=?
                  AND status IN ('queued','delivered','processing')
                """,
                (now, relay_id, target_profile),
            )
            if completed_by and cursor.rowcount:
                conn.execute(
                    "UPDATE hib_handoffs SET completed_by=?, completed_at=? WHERE relay_id=?",
                    (completed_by, now, relay_id),
                )
            return cursor.rowcount > 0

    def fail(
        self,
        relay_id: str,
        *,
        target_profile: str,
        error: str,
        counts_attempt: bool = True,
        source_notified: bool = False,
    ) -> dict[str, Any]:
        """Fail a processing attempt.

        Counted failures reaching ``max_attempts`` go to the dead-letter (FR-031).
        Availability failures pass ``counts_attempt=False`` so the message never
        dead-letters while the target is simply unreachable (FR-006, §8.6).
        """
        now = time.time()
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM hib_messages WHERE relay_id=? AND target_profile=?",
                (relay_id, target_profile),
            ).fetchone()
            if row is None:
                raise BridgeError("relay_payload_invalid", "Сообщение не найдено в шине.")
            attempts = int(row["attempts"]) + (1 if counts_attempt else 0)
            notified = int(row["source_notified"]) or (1 if source_notified else 0)
            if attempts >= self.max_attempts:
                status = "dead"
                lease_until = None
                next_attempt_at = None
            else:
                status = "queued"
                lease_until = None
                next_attempt_at = now + self._backoff_delay(attempts)
            conn.execute(
                """
                UPDATE hib_messages
                SET status=?, attempts=?, lease_until=?, next_attempt_at=?, last_error=?,
                    source_notified=?, updated_at=?
                WHERE relay_id=? AND target_profile=?
                """,
                (status, attempts, lease_until, next_attempt_at, error, notified, now,
                 relay_id, target_profile),
            )
        updated = self.get(relay_id, target_profile=target_profile)
        if updated is not None and updated["status"] == "dead":
            raise BridgeError("bus_dead_letter")
        return updated

    def mark_source_notified(self, relay_id: str, *, target_profile: str) -> bool:
        """Record that the source was already told about the failure (ack exactly once)."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE hib_messages SET source_notified=1, updated_at=? WHERE relay_id=? AND target_profile=?",
                (time.time(), relay_id, target_profile),
            )
            return cursor.rowcount > 0

    def set_status(self, relay_id: str, status: str, *, target_profile: str, error: str = "") -> bool:
        if status not in STATES:
            raise ValueError(f"unknown bus status: {status}")
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE hib_messages SET status=?, last_error=?, lease_until=NULL, updated_at=? "
                "WHERE relay_id=? AND target_profile=?",
                (status, error, time.time(), relay_id, target_profile),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------- inspection

    def counts(self) -> dict[str, int]:
        rows = self._db.query("SELECT status, COUNT(*) AS n FROM hib_messages GROUP BY status")
        counts = {state: 0 for state in STATES}
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts

    def list_by_status(self, status: str, *, target_profile: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if target_profile:
            rows = self._db.query(
                "SELECT * FROM hib_messages WHERE status=? AND target_profile=? ORDER BY created_at LIMIT ?",
                (status, target_profile, limit),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM hib_messages WHERE status=? ORDER BY created_at LIMIT ?", (status, limit)
            )
        return [row_to_dict(row) for row in rows]

    def find_for_reply(self, *, target_profile: str, payload_text: str) -> dict[str, Any] | None:
        """The delivered relay whose injected text matches a finished turn (reply correlation)."""
        row = self._db.query_one(
            """
            SELECT * FROM hib_messages
            WHERE target_profile=? AND status IN ('delivered','processing')
              AND kind IN ('chat.message','handoff.request')
              AND payload_text=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (target_profile, payload_text),
        )
        return row_to_dict(row)

    def correlation_id_for(self, relay_id: str) -> str | None:
        row = self._db.query_one("SELECT correlation_id FROM hib_messages WHERE relay_id=?", (relay_id,))
        return row["correlation_id"] if row else None

    def route_id_for(self, relay_id: str) -> str:
        row = self._db.query_one("SELECT route_id FROM hib_messages WHERE relay_id=?", (relay_id,))
        return row["route_id"] if row else ""

    def purge_done(self, *, older_than: float) -> int:
        """Retention (§12): messages done before *older_than* are removed; the ledger stays."""
        with self._db.transaction() as conn:
            relay_ids = [
                row["relay_id"]
                for row in conn.execute(
                    "SELECT relay_id FROM hib_messages WHERE status='done' AND updated_at < ?",
                    (older_than,),
                ).fetchall()
            ]
            for relay_id in relay_ids:
                conn.execute("DELETE FROM hib_handoffs WHERE relay_id=?", (relay_id,))
                conn.execute("DELETE FROM hib_artifacts WHERE relay_id=?", (relay_id,))
            cursor = conn.execute(
                "DELETE FROM hib_messages WHERE status='done' AND updated_at < ?", (older_than,)
            )
            return cursor.rowcount

    # ---------------------------------------------------------------- private

    def _backoff_delay(self, attempts: int) -> float:
        if attempts <= 0:
            return float(self.backoff_seconds[0])
        index = min(attempts - 1, len(self.backoff_seconds) - 1)
        return float(self.backoff_seconds[index])


def new_ledger_id() -> str:
    return str(uuid.uuid4())

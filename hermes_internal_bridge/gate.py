"""Trigger gate (spec §7.2): relay/forwarded messages never reach the agent.

Framework-level fail-open means a raising callback would let the message
through (``run_inbound_hooks.py`` logs and proceeds), so the whole evaluation
runs under its own ``try/except``: messages with relay metadata are ALWAYS
skipped (fail-closed, FR-013), ordinary messages fail open with a
``gate_degraded`` ledger event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .envelope import strip_relay_marker
from .ledger import Ledger

logger = logging.getLogger("hib.gate")

# Attribute names platforms use for forwarded origins (checked defensively).
_FORWARD_ATTRS = ("forward_origin", "forward_from", "forward_from_chat", "forwarded", "is_forward")


@dataclass
class RelayDetection:
    relay_id: str | None
    hop: int | None
    is_forwarded: bool


@dataclass
class GateVerdict:
    action: str  # "allow" | "skip"
    reason: str  # spec §10 code or ""
    detection: RelayDetection


class TriggerGate:
    """Decides whether an inbound event may reach the agent (FR-008..FR-013)."""

    def __init__(self, ledger: Ledger | None = None, *, ignore_forwarded_mentions: bool = True,
                 max_hops: int = 1) -> None:
        self._ledger = ledger
        self.ignore_forwarded_mentions = ignore_forwarded_mentions
        self.max_hops = max_hops

    # ------------------------------------------------------------- detection

    def detect(self, event: Any) -> RelayDetection:
        text = getattr(event, "text", "") or ""
        _, relay_id, hop = strip_relay_marker(text)
        return RelayDetection(relay_id=relay_id, hop=hop, is_forwarded=self._is_forwarded(event))

    def _is_forwarded(self, event: Any) -> bool:
        for attr in _FORWARD_ATTRS:
            value = getattr(event, attr, None)
            if value:
                return True
        raw = getattr(event, "raw_message", None)
        if raw is not None:
            for attr in _FORWARD_ATTRS:
                if getattr(raw, attr, None):
                    return True
        return False

    # -------------------------------------------------------------- decision

    def evaluate(self, event: Any, *, relay_enabled: bool = True) -> GateVerdict:
        try:
            detection = self.detect(event)
            marked = detection.relay_id is not None or detection.is_forwarded
            if not marked:
                return GateVerdict(action="allow", reason="", detection=detection)
            # Relay-marked or forwarded messages never run the agent (FR-008),
            # even when the text mentions the bot (FR-009 default true).
            if detection.relay_id is not None and detection.hop is not None and detection.hop > self.max_hops:
                self._ledger_event(detection.relay_id, "relay_hop_limit",
                                   details={"hop": detection.hop, "relay_id": detection.relay_id})
                return GateVerdict(action="skip", reason="relay_hop_limit", detection=detection)
            if detection.relay_id is not None and not detection.is_forwarded:
                self._ledger_event(detection.relay_id, "relay_filtered_relay",
                                   details={"relay_id": detection.relay_id})
                return GateVerdict(action="skip", reason="relay_filtered_relay", detection=detection)
            if not self.ignore_forwarded_mentions:
                # Operator explicitly allowed forwarded mentions: only true relay
                # markers are filtered; this is the FR-009 escape hatch.
                if detection.relay_id is None:
                    return GateVerdict(action="allow", reason="", detection=detection)
            self._ledger_event(
                detection.relay_id or "", "relay_filtered_forwarded",
                details={"relay_id": detection.relay_id, "forwarded": True},
            )
            return GateVerdict(action="skip", reason="relay_filtered_forwarded", detection=detection)
        except Exception as exc:
            # Internal gate error (FR-013): re-detect conservatively — anything
            # carrying relay metadata is skipped (fail-closed), ordinary messages
            # fail open with a gate_degraded event.
            marked = self._conservative_marked(event)
            if marked:
                logger.warning("gate error on relay-marked message: %s", exc)
                return GateVerdict(action="skip", reason="relay_filtered_forwarded",
                                   detection=RelayDetection(None, None, True))
            logger.warning("gate degraded on ordinary message: %s", exc)
            self._ledger_event("", "gate_degraded", details={"error": str(exc)})
            return GateVerdict(action="allow", reason="gate_degraded",
                               detection=RelayDetection(None, None, False))

    def _conservative_marked(self, event: Any) -> bool:
        """Best-effort relay detection when the main path failed."""
        try:
            text = getattr(event, "text", "") or ""
            _, relay_id, _ = strip_relay_marker(text)
            if relay_id is not None:
                return True
            return self._is_forwarded(event)
        except Exception:
            return False

    def _ledger_event(self, relay_id: str, event: str, *, details: dict | None = None) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.event(relay_id, event, details=details)
        except Exception:  # ledger failures must not affect the gate decision
            logger.debug("ledger write failed for %s", event, exc_info=True)

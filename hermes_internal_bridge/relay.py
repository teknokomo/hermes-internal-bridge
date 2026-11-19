"""Intake (spec §7.1, §6.2): the ``pre_gateway_dispatch`` flow.

Normalizes the inbound event, runs the trigger gate, resolves the route,
enqueues the envelope on the shared bus, and schedules exactly one ack. Voice
messages are transcribed asynchronously in the SOURCE profile after the hook
returns (source-profile transcription rule): only the transcript and a reference to the
original copied into the HIB area ever reach the bus.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .envelope import Envelope, new_chat_message_envelope, new_correlation_id, new_relay_id
from .errors import BridgeError
from .transcription import copy_audio_to_hib_area

logger = logging.getLogger("hib.relay")

VOICE_TYPES = frozenset({"voice", "audio"})

AckScheduler = Callable[[dict[str, Any], str], Any]


def extract_source(event: Any, own_profile: str) -> dict[str, Any] | None:
    """Build the envelope source block from a gateway MessageEvent; None when unusable."""
    src = getattr(event, "source", None)
    if src is None:
        return None
    platform = getattr(src, "platform", None)
    platform_str = getattr(platform, "value", platform) if platform is not None else ""
    chat_id = str(getattr(src, "chat_id", "") or "")
    if not chat_id:
        return None
    message_id = str(getattr(event, "message_id", "") or getattr(src, "message_id", "") or "")
    return {
        "instance_id": "local",
        "profile": own_profile,
        "platform": str(platform_str or ""),
        "user_id": str(getattr(src, "user_id", "") or ""),
        "chat_id": chat_id,
        "thread_id": str(getattr(src, "thread_id", "") or ""),
        "message_id": message_id,
    }


def message_type_of(event: Any) -> str:
    mt = getattr(event, "message_type", None)
    value = getattr(mt, "value", mt)
    return str(value or "text")


def first_audio_path(event: Any) -> Path | None:
    """The first voice/audio attachment path (gateway keeps local paths in ``media_urls``)."""
    urls = list(getattr(event, "media_urls", None) or [])
    types = list(getattr(event, "media_types", None) or [])
    for index, url in enumerate(urls):
        media_type = str(types[index]) if index < len(types) else ""
        if media_type.lower() in VOICE_TYPES and url:
            return Path(url)
    if urls and not types and urls[0]:
        return Path(urls[0])
    return None


def route_target_of(route) -> dict[str, Any]:
    return {
        "instance_id": "local",
        "profile": str(route.target.get("profile", "")),
        "chat_id": str(route.target.get("chat_id", "") or ""),
        "thread_id": str(route.target.get("thread_id", "") or ""),
    }


class RelayIntake:
    """Handles one gateway inbound event end-to-end."""

    def __init__(
        self,
        *,
        profile: str,
        bus,
        routes,
        ledger,
        gate,
        ack_scheduler: AckScheduler | None = None,
        relay_enabled: bool = False,
        max_hops: int = 1,
        max_payload_bytes: int = 65536,
        audio_area: Path | None = None,
        transcription_provider=None,
        ack_text: str = "передано",
    ) -> None:
        self.profile = profile
        self.bus = bus
        self.routes = routes
        self.ledger = ledger
        self.gate = gate
        self.ack_scheduler = ack_scheduler
        self.relay_enabled = relay_enabled
        self.max_hops = max_hops
        self.max_payload_bytes = max_payload_bytes
        self.audio_area = audio_area
        self.transcription_provider = transcription_provider
        self.ack_text = ack_text

    # ------------------------------------------------------------------ hook

    def handle_event(self, event: Any, gateway: Any = None, session_store: Any = None) -> dict[str, Any] | None:
        """The hook callback body; returns the hook decision dict or None (allow)."""
        verdict = self.gate.evaluate(event, relay_enabled=self.relay_enabled)
        if verdict.action == "skip":
            return {"action": "skip", "reason": verdict.reason}
        if not self.relay_enabled:
            return None
        source = extract_source(event, self.profile)
        if source is None:
            return None
        decision = self.routes.resolve(source)
        if decision.error is not None:
            self._ledger(decision.error.code, "", route_id="", details={
                "specificity": decision.specificity,
                "candidates": [r.route_id for r in decision.candidates],
                "conflicts": list(decision.conflict_route_ids),
                "source_chat": source["chat_id"],
            })
            # Fail-closed: no relay, no nearest-profile guessing; the message
            # keeps its ordinary dispatch (FR-017).
            return None
        route = decision.route
        try:
            if message_type_of(event) in VOICE_TYPES:
                self._handle_voice(event, source, route)
            else:
                self._handle_text(event, source, route)
        except BridgeError as exc:
            self._ledger(exc.code, "", route_id=route.route_id, details={"error": exc.message})
            self._ack(source, f"Ошибка: {exc.message}")
            return {"action": "skip", "reason": exc.code}
        except Exception as exc:  # intake must never break ordinary dispatch
            logger.exception("intake failed unexpectedly: %s", exc)
            self._ledger("gate_degraded", "", route_id=route.route_id, details={"error": str(exc)})
            return None
        return {"action": "skip", "reason": "relay_enqueued"}

    # ----------------------------------------------------------------- paths

    def _handle_text(self, event: Any, source: dict[str, Any], route) -> None:
        text = str(getattr(event, "text", "") or "").strip()
        envelope = new_chat_message_envelope(
            source=source,
            destination=route_target_of(route),
            text=text,
            hop=1,
        )
        self._enqueue(envelope, route)
        self._ack(source, None)

    def _handle_voice(self, event: Any, source: dict[str, Any], route) -> None:
        audio_path = first_audio_path(event)
        if audio_path is None:
            raise BridgeError("relay_transcript_failed")
        if self.audio_area is None:
            raise BridgeError("relay_transcript_failed", "Не настроена область хранения аудио HIB.")
        if self.transcription_provider is None:
            raise BridgeError("relay_transcript_failed")
        relay_id = new_relay_id()
        correlation_id = new_correlation_id()
        ref = copy_audio_to_hib_area(audio_path, Path(self.audio_area), relay_id, self.profile)
        envelope = new_chat_message_envelope(
            relay_id=relay_id,
            correlation_id=correlation_id,
            source=source,
            destination=route_target_of(route),
            text="",
            is_voice_transcript=True,
            attachments=[ref],
        )
        worker = threading.Thread(
            target=self._transcribe_worker, args=(envelope, route), name=f"hib-transcribe-{relay_id[:8]}",
            daemon=True,
        )
        worker.start()
        self._ack(source, "принято: голосовое, распознаю…")

    def _transcribe_worker(self, envelope: Envelope, route) -> None:
        """Source-profile transcription rule: transcription runs in the source profile, off the hook path."""
        try:
            ref = (envelope.payload.get("attachments") or [{}])[0]
            text = self.transcription_provider.transcribe(Path(ref.get("uri", "")))
            envelope.payload["text"] = text
            envelope.validate(max_hops=self.max_hops, max_payload_bytes=self.max_payload_bytes)
        except BridgeError as exc:
            self._ledger(exc.code, envelope.relay_id, route_id=route.route_id, details={"error": exc.message})
            self._ack(dict(envelope.source), f"Ошибка: {exc.message}")
            return
        except Exception as exc:
            self._ledger("relay_transcript_failed", envelope.relay_id, route_id=route.route_id,
                         details={"error": str(exc)})
            return
        try:
            self._enqueue(envelope, route)
        except BridgeError as exc:
            self._ledger(exc.code, envelope.relay_id, route_id=route.route_id, details={"error": exc.message})
        else:
            self._ledger("relay_transcribed", envelope.relay_id, route_id=route.route_id,
                         details={"chars": len(envelope.payload_text())})

    # -------------------------------------------------------------- internals

    def _enqueue(self, envelope: Envelope, route) -> None:
        envelope.validate(max_hops=self.max_hops, max_payload_bytes=self.max_payload_bytes)
        try:
            self.bus.enqueue(envelope, route_id=route.route_id)
        except BridgeError as exc:
            if exc.code == "relay_duplicate":
                self._ledger("relay_duplicate", envelope.relay_id, route_id=route.route_id)
                self._ack(dict(envelope.source), None)
                return
            raise
        self._ledger("relay_enqueued", envelope.relay_id, route_id=route.route_id, details={
            "route_id": route.route_id,
            "specificity": route.specificity(),
            "target_profile": envelope.target_profile,
            "target_chat": envelope.destination.get("chat_id", ""),
            "payload_size": envelope.payload_size(),
            "is_voice_transcript": bool(envelope.payload.get("is_voice_transcript")),
        })

    def _ack(self, source: dict[str, Any], text: str | None) -> None:
        """Schedule exactly one ack to the source chat; failures never break intake."""
        if self.ack_scheduler is None:
            return
        message = text if text is not None else self.ack_text
        try:
            self.ack_scheduler(source, message)
        except Exception:
            logger.debug("ack scheduling failed", exc_info=True)

    def _ledger(self, event: str, relay_id: str, *, route_id: str = "", details: dict | None = None) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.event(relay_id, event, route_id=route_id, details=details)
        except Exception:
            logger.debug("ledger write failed for %s", event, exc_info=True)

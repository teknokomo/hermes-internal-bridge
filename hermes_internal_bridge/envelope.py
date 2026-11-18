"""Relay envelope (spec §9.2): the single unit of transfer between profiles."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import BridgeError

ENVELOPE_VERSION = "1"
LOCAL_INSTANCE_ID = "local"

KINDS: frozenset[str] = frozenset(
    {
        "chat.message",
        "handoff.request",
        "handoff.result",
        "task.request",
        "task.result",
        "artifact.ready",
        "health",
    }
)

# Reserved types are validated but always rejected in 0.1.0 (FR-050, fail-closed).
RESERVED_KINDS: frozenset[str] = frozenset({"approval.request", "approval.decision"})

PRIORITIES: frozenset[str] = frozenset({"low", "normal", "high"})

# Human-readable relay header embedded in generated messages so anything that
# re-enters the gateway is detectable by the trigger gate (spec §6.3).
RELAY_MARKER_RE = re.compile(r"\[hib:r=([0-9a-fA-F-]{16,64});h=(\d+)\]")


def relay_marker(relay_id: str, hop: int) -> str:
    return f"[hib:r={relay_id};h={hop}]"


def strip_relay_marker(text: str) -> tuple[str, str | None, int | None]:
    """Split a relay header line off *text*; returns (clean_text, relay_id, hop)."""
    if not isinstance(text, str):
        return text, None, None
    match = RELAY_MARKER_RE.search(text)
    if not match:
        return text, None, None
    clean = (text[: match.start()] + text[match.end() :]).strip()
    return clean, match.group(1), int(match.group(2))


def new_relay_id() -> str:
    return str(uuid.uuid4())


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def _require_str(mapping: dict[str, Any], key: str, where: str, *, allow_empty: bool = False) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise BridgeError("relay_payload_invalid", f"{where}.{key} должен быть строкой.")
    if not allow_empty and not value.strip():
        raise BridgeError("relay_payload_invalid", f"{where}.{key} не может быть пустым.")
    return value


def _require_int(mapping: dict[str, Any], key: str, where: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeError("relay_payload_invalid", f"{where}.{key} должен быть числом.")
    return value


def _sub_mapping(mapping: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = mapping.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BridgeError("relay_payload_invalid", f"{where}.{key} должен быть объектом.")
    return value


@dataclass
class Envelope:
    """Structured transfer envelope; the exact JSON shape is spec §9.2."""

    relay_id: str
    correlation_id: str
    kind: str
    source: dict[str, Any]
    destination: dict[str, Any]
    payload: dict[str, Any]
    version: str = ENVELOPE_VERSION
    instance_id: str = LOCAL_INSTANCE_ID
    causation_id: str | None = None
    hop: int = 1
    ttl_seconds: int = 300
    reply_to: str | None = None
    relay_mirror: bool = False

    # ------------------------------------------------------------------ shape

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "instance_id": self.instance_id,
            "relay_id": self.relay_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "kind": self.kind,
            "hop": self.hop,
            "ttl_seconds": self.ttl_seconds,
            "source": dict(self.source),
            "destination": dict(self.destination),
            "reply_to": self.reply_to,
            "relay_mirror": self.relay_mirror,
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Envelope":
        if not isinstance(data, dict):
            raise BridgeError("relay_payload_invalid", "Конверт должен быть объектом.")
        try:
            return cls(
                relay_id=_require_str(data, "relay_id", "envelope"),
                correlation_id=_require_str(data, "correlation_id", "envelope"),
                kind=_require_str(data, "kind", "envelope"),
                source=dict(_sub_mapping(data, "source", "envelope")),
                destination=dict(_sub_mapping(data, "destination", "envelope")),
                payload=dict(_sub_mapping(data, "payload", "envelope")),
                version=data.get("version", ENVELOPE_VERSION) or ENVELOPE_VERSION,
                instance_id=data.get("instance_id", LOCAL_INSTANCE_ID) or LOCAL_INSTANCE_ID,
                causation_id=data.get("causation_id"),
                hop=_require_int(data, "hop", "envelope"),
                ttl_seconds=_require_int(data, "ttl_seconds", "envelope"),
                reply_to=data.get("reply_to"),
                relay_mirror=bool(data.get("relay_mirror", False)),
            )
        except BridgeError:
            raise
        except Exception as exc:  # malformed in an unexpected way
            raise BridgeError("relay_payload_invalid", f"Конверт не разобран: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> "Envelope":
        try:
            data = json.loads(text)
        except Exception as exc:
            raise BridgeError("relay_payload_invalid", "Конверт не является корректным JSON.") from exc
        return cls.from_dict(data)

    # -------------------------------------------------------------- validation

    def validate(self, *, max_hops: int = 1, max_payload_bytes: int = 65536) -> None:
        """Validate the envelope against the spec rules; raises BridgeError on failure."""
        if self.version != ENVELOPE_VERSION:
            raise BridgeError("relay_payload_invalid", "Неподдерживаемая версия конверта.")
        if self.instance_id != LOCAL_INSTANCE_ID:
            # FR-051: non-local instances are rejected in 0.1.0.
            raise BridgeError("federation_not_supported")
        if self.kind in RESERVED_KINDS:
            # FR-050: reserved types validate but always fail in 0.1.0.
            raise BridgeError("approval_not_supported")
        if self.kind not in KINDS:
            raise BridgeError("relay_payload_invalid", "Неизвестный тип конверта.")
        if not self.source:
            raise BridgeError("relay_payload_invalid", "Пустой источник.")
        if not self.destination:
            raise BridgeError("relay_payload_invalid", "Пустой получатель.")
        _require_str(self.source, "profile", "source")
        _require_str(self.destination, "profile", "destination")
        _require_str(self.source, "chat_id", "source", allow_empty=True)
        _require_str(self.destination, "chat_id", "destination", allow_empty=True)
        if self.hop < 1:
            raise BridgeError("relay_payload_invalid", "hop не может быть меньше 1.")
        if self.hop > max_hops:
            # FR-010: hop > max_hops is rejected.
            raise BridgeError("relay_hop_limit")
        if self.ttl_seconds <= 0:
            raise BridgeError("relay_payload_invalid", "ttl_seconds должен быть положительным.")
        priority = self.payload.get("priority", "normal")
        if priority not in PRIORITIES:
            raise BridgeError("relay_payload_invalid", "Недопустимый приоритет.")
        if not self.relay_mirror and self.kind == "chat.message":
            text = self.payload.get("text")
            if not isinstance(text, str) or not text.strip():
                # Voice envelopes may be empty until the source-profile transcription
                # fills payload.text (FR-002); everything else is rejected before enqueue.
                if not self.payload.get("is_voice_transcript"):
                    raise BridgeError("relay_payload_invalid", "Пустой payload.")
        self._validate_attachments()
        size = len(self.to_json().encode("utf-8"))
        if size > max_payload_bytes:
            raise BridgeError(
                "relay_payload_too_large", f"Размер конверта {size} байт превышает лимит {max_payload_bytes}."
            )

    def _validate_attachments(self) -> None:
        from .artifacts import validate_artifact_ref  # local import avoids a cycle

        attachments = self.payload.get("attachments") or []
        if not isinstance(attachments, list):
            raise BridgeError("relay_payload_invalid", "attachments должен быть списком.")
        for ref in attachments:
            validate_artifact_ref(ref)

    # ----------------------------------------------------------------- helpers

    @property
    def target_profile(self) -> str:
        return str(self.destination.get("profile", ""))

    @property
    def source_profile(self) -> str:
        return str(self.source.get("profile", ""))

    def payload_text(self) -> str:
        text = self.payload.get("text")
        return text if isinstance(text, str) else ""

    def rendered_text(self) -> str:
        """The text injected into the target profile: relay header + payload text."""
        header = relay_marker(self.relay_id, self.hop)
        return f"{header} {self.payload_text()}".strip()

    def payload_size(self) -> int:
        return len(self.to_json().encode("utf-8"))


def new_chat_message_envelope(
    *,
    relay_id: str | None = None,
    correlation_id: str | None = None,
    source: dict[str, Any],
    destination: dict[str, Any],
    text: str,
    hop: int = 1,
    ttl_seconds: int = 300,
    relay_mirror: bool = False,
    is_voice_transcript: bool = False,
    attachments: list | None = None,
    payload: dict[str, Any] | None = None,
) -> Envelope:
    """Build a ``chat.message`` envelope with the §9.2 payload defaults."""
    merged: dict[str, Any] = {
        "text": text,
        "is_voice_transcript": bool(is_voice_transcript),
        "attachments": list(attachments or []),
        "summary": None,
        "constraints": [],
        "expected_result": None,
        "deadline_at": None,
        "priority": "normal",
    }
    if payload:
        merged.update(payload)
    payload = merged
    return Envelope(
        relay_id=relay_id or new_relay_id(),
        correlation_id=correlation_id or new_correlation_id(),
        kind="chat.message",
        source=dict(source),
        destination=dict(destination),
        payload=payload,
        hop=hop,
        ttl_seconds=ttl_seconds,
        relay_mirror=relay_mirror,
    )

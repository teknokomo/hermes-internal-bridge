"""Artifact references (spec §7.7): links only, never bytes.

An artifact is described as ``{artifact_id, kind, size_bytes, sha256, uri|document_id,
owner_profile}``. Base64 payloads and inline content are rejected; secrets are
rejected outright (FR-041).
"""

from __future__ import annotations

import re
from typing import Any

from .errors import BridgeError
from .secrets import find_secrets

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_KEYS = ("artifact_id", "kind", "size_bytes", "sha256", "owner_profile")
FORBIDDEN_KEYS = ("content", "content_b64", "data", "base64", "bytes", "payload_b64", "content_base64")


def validate_artifact_ref(ref: Any) -> dict[str, Any]:
    """Validate one artifact reference; raises BridgeError on violation."""
    if not isinstance(ref, dict):
        raise BridgeError("relay_payload_invalid", "Артефакт должен быть объектом-ссылкой.")
    for key in FORBIDDEN_KEYS:
        if key in ref:
            # FR-039: bytes never travel in the envelope, base64 is forbidden.
            raise BridgeError("relay_payload_invalid", "Артефакт передаётся ссылкой, без байтов.")
    for key in REQUIRED_KEYS:
        if key not in ref:
            raise BridgeError("relay_payload_invalid", f"В артефакте нет обязательного поля {key}.")
    if not isinstance(ref["artifact_id"], str) or not ref["artifact_id"].strip():
        raise BridgeError("relay_payload_invalid", "artifact_id должен быть непустой строкой.")
    if not isinstance(ref["kind"], str) or not ref["kind"].strip():
        raise BridgeError("relay_payload_invalid", "kind артефакта должен быть непустой строкой.")
    size = ref["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise BridgeError("relay_payload_invalid", "size_bytes должен быть неотрицательным числом.")
    if not isinstance(ref["sha256"], str) or not SHA256_RE.match(ref["sha256"]):
        raise BridgeError("relay_payload_invalid", "sha256 должен быть hex-строкой из 64 символов.")
    if not isinstance(ref["owner_profile"], str) or not ref["owner_profile"].strip():
        raise BridgeError("relay_payload_invalid", "owner_profile должен быть непустой строкой.")
    uri = ref.get("uri")
    document_id = ref.get("document_id")
    if (uri is None) == (document_id is None):
        raise BridgeError("relay_payload_invalid", "Нужен ровно один из uri|document_id.")
    if uri is not None and not isinstance(uri, str):
        raise BridgeError("relay_payload_invalid", "uri должен быть строкой.")
    if document_id is not None and not isinstance(document_id, str):
        raise BridgeError("relay_payload_invalid", "document_id должен быть строкой.")
    # FR-041: secrets, tokens and .env content can never be artifacts.
    serialized = " ".join(str(ref.get(key, "")) for key in ("uri", "kind", "artifact_id"))
    if find_secrets(serialized) or _looks_like_env_reference(ref):
        raise BridgeError("relay_acl_denied")
    return ref


def _looks_like_env_reference(ref: dict[str, Any]) -> bool:
    uri = str(ref.get("uri", "")).lower()
    return uri.endswith((".env", ".env.local", "auth.json", "state.db")) or "/.env" in uri or "\\auth.json" in uri

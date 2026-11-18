"""Voice transcription in the SOURCE profile (Omisa's addition #1).

Transcription runs asynchronously after ``pre_gateway_dispatch``: the intake
hook only copies the audio into the HIB area of the source profile and queues
the work. Only the transcript text and a reference to the copied original ever
enter the shared bus; the consumer in the target profile neither reads nor
transcribes audio.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Protocol

from .errors import BridgeError
from .paths import ensure_private_dir


class TranscriptionProvider(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class NullProvider:
    """Default provider: no transcription backend configured (operator must set one)."""

    def transcribe(self, audio_path: Path) -> str:
        raise BridgeError("relay_transcript_failed", "Поставщик транскрипции не настроен.")


class OpenAICompatProvider:
    """Minimal multipart client for an OpenAI-compatible ``/audio/transcriptions`` endpoint.

    The API key is taken from the environment variable named by ``api_key_env``
    — the plugin never reads ``.env`` files itself; the host process already has
    the profile environment loaded.
    """

    def __init__(self, *, api_base: str, model: str, api_key_env: str, language: str = "") -> None:
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.language = language

    def transcribe(self, audio_path: Path) -> str:
        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise BridgeError(
                "relay_transcript_failed",
                f"Ключ транскрипции не найден в переменной {self.api_key_env}.",
            )
        boundary = uuid.uuid4().hex
        content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
        fields = [("model", self.model), ("response_format", "json")]
        if self.language:
            fields.append(("language", self.language))
        body: list[bytes] = []
        for name, value in fields:
            body.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )
        body.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{audio_path.name}\"\r\nContent-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        body.append(audio_path.read_bytes())
        body.append(f"\r\n--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"{self.api_base}/audio/transcriptions",
            data=b"".join(body),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except BridgeError:
            raise
        except urllib.error.HTTPError as exc:
            raise BridgeError(
                "relay_transcript_failed", f"Поставщик транскрипции вернул ошибку HTTP {exc.code}."
            ) from exc
        except Exception as exc:
            raise BridgeError("relay_transcript_failed", "Поставщик транскрипции недоступен.") from exc
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BridgeError("relay_transcript_failed", "Поставщик вернул пустой транскрипт.")
        return text


def provider_from_config(config: dict[str, Any]) -> TranscriptionProvider:
    """Build the provider from the ``transcription`` config section."""
    provider = str(config.get("provider", "none"))
    if provider == "none":
        return NullProvider()
    if provider == "openai-compat":
        api_base = str(config.get("api_base", "")).strip()
        if not api_base:
            raise BridgeError("relay_transcript_failed", "Не настроен api_base поставщика транскрипции.")
        return OpenAICompatProvider(
            api_base=api_base,
            model=str(config.get("model", "whisper-1")),
            api_key_env=str(config.get("api_key_env", "")),
            language=str(config.get("language", "")),
        )
    raise BridgeError("relay_transcript_failed", f"Неизвестный поставщик транскрипции: {provider}.")


def copy_audio_to_hib_area(audio_path: Path, area_dir: Path, relay_id: str,
                           owner_profile: str) -> dict[str, Any]:
    """Copy the original voice clip into the source profile's HIB area (FR-002, FR-038).

    Returns an artifact reference; the audio bytes themselves never enter the bus.
    """
    src = Path(audio_path)
    if not src.is_file():
        raise BridgeError("relay_transcript_failed", "Оригинал голосового не найден на диске.")
    ensure_private_dir(area_dir)
    data = src.read_bytes()
    suffix = src.suffix or ".ogg"
    dest = Path(area_dir) / f"{relay_id}{suffix}"
    dest.write_bytes(data)
    return {
        "artifact_id": str(uuid.uuid4()),
        "kind": "voice-original",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "uri": str(dest),
        "owner_profile": owner_profile,
    }

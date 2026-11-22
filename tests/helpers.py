"""Shared fakes and builders for HIB tests (no network, no real adapters)."""

from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_internal_bridge.db import Database  # noqa: E402
from hermes_internal_bridge.plugin import CONFIG_DEFAULTS, build_components  # noqa: E402


class FakeSource:
    def __init__(self, platform="telegram", user_id="user-1", chat_id="dm-chat-1",
                 thread_id="", message_id="m-1"):
        self.platform = platform
        self.user_id = user_id
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.message_id = message_id


class FakeEvent:
    """Minimal MessageEvent stand-in with the fields the gateway contract exposes."""

    def __init__(self, text="привет", *, source=None, message_id="m-1", message_type="text",
                 media_urls=None, media_types=None, raw_message=None, **extra):
        self.text = text
        self.source = source or FakeSource()
        self.message_id = message_id
        self.message_type = message_type
        self.media_urls = list(media_urls or [])
        self.media_types = list(media_types or [])
        self.raw_message = raw_message
        for key, value in extra.items():
            setattr(self, key, value)


class FakeProvider:
    """Scriptable transcription provider (transcription provider test seam)."""

    def __init__(self, text="распознанный текст", error=None):
        self.text = text
        self.error = error
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> str:
        self.calls.append(audio_path)
        if self.error is not None:
            raise self.error
        return self.text


class FakeInjector:
    """Scriptable ctx.inject_message stand-in."""

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def __call__(self, origin=None, content="", **kwargs):
        self.calls.append({"origin": origin, "content": content, **kwargs})
        return self.result


class FakeDisplaySender:
    """Scriptable adapter-side display sender (mirror copies, notices)."""

    def __init__(self, result=True):
        self.result = result
        self.sent = []

    def __call__(self, destination, text):
        self.sent.append((dict(destination), text))
        return self.result


def merged_config(**overrides):
    config = copy.deepcopy(CONFIG_DEFAULTS)
    for section, values in overrides.items():
        config[section].update(values)
    return config


def make_components(tmp_path, *, profile="default", relay_enabled=True, roster=None, **overrides):
    """Build a real component graph over a temporary database with fakes for the gateway."""
    config = merged_config(**overrides)
    config["relay"]["enabled"] = bool(relay_enabled)
    if roster is not None:
        config["directory"]["profiles"] = roster
    db = Database(tmp_path / "hib.sqlite3")
    acks: list[tuple[dict, str]] = []

    def ack_scheduler(source, text):
        acks.append((dict(source), text))

    components = build_components(profile=profile, config=config, db=db, ack_scheduler=ack_scheduler,
                                  thread_ack_scheduler=ack_scheduler)
    components.consumer.ack_scheduler = ack_scheduler
    return components, acks


def make_route(route_id="r1", *, source=None, target=None, mode="handoff", priority=0, enabled=True):
    from hermes_internal_bridge.routes import Route

    return Route(
        route_id=route_id,
        source=source or {"platform": "telegram", "user_id": "user-1", "chat_id": "dm-chat-1",
                          "thread_id": ""},
        target=target or {"profile": "target-profile", "chat_id": "topic-1", "thread_id": "t-9"},
        mode=mode,
        priority=priority,
        enabled=enabled,
    )


def make_envelope_kwargs(**overrides):
    """kwargs for ``new_chat_message_envelope``; ``kind`` is applied by the caller."""
    from hermes_internal_bridge.envelope import new_relay_id

    overrides.pop("kind", None)  # applied as an attribute by enqueue_message
    values = {
        "relay_id": overrides.pop("relay_id", None) or str(uuid.uuid4()),
        "correlation_id": overrides.pop("correlation_id", None) or str(uuid.uuid4()),
        "source": overrides.pop(
            "source",
            {"instance_id": "local", "profile": "source-profile", "platform": "telegram",
             "user_id": "user-1", "chat_id": "dm-chat-1", "thread_id": "", "message_id": "m-1"},
        ),
        "destination": overrides.pop(
            "destination",
            {"instance_id": "local", "profile": "target-profile", "chat_id": "topic-1",
             "thread_id": "t-9"},
        ),
        "text": overrides.pop("text", "задача для профиля"),
    }
    values.update(overrides)
    return values


def enqueue_message(bus, *, route_id: str = "", **kwargs):
    from hermes_internal_bridge.envelope import new_chat_message_envelope

    kind = kwargs.pop("kind", None)
    envelope = new_chat_message_envelope(**make_envelope_kwargs(**kwargs))
    if kind is not None:
        envelope.kind = kind
    bus.enqueue(envelope, route_id=route_id)
    return envelope


def drain_until(predicate, timeout=5.0, step=0.05):
    """Wait for a background-thread condition (voice transcription worker)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


import time  # noqa: E402  (kept last: used by drain_until only)

"""Plugin wiring: config → components → hooks + tools.

Everything is defensive: missing Hermes APIs (plain pytest runs) degrade to
component-level operation with injected fakes instead of crashing the host.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from . import paths
from .bus import MessageBus
from .consumer import ProfileConsumer
from .db import Database
from .directory import Directory
from .gate import TriggerGate
from .ledger import Ledger
from .relay import RelayIntake
from .routes import RouteRegistry
from .tools import build_tool_handlers
from .transcription import provider_from_config

logger = logging.getLogger("hib.plugin")

PLUGIN_NAME = "hermes-internal-bridge"
TOOLSET = "hib"

CONFIG_DEFAULTS: dict[str, dict[str, Any]] = {
    "relay": {
        "enabled": False,
        "ignore_forwarded_mentions": True,
        "max_hops": 1,
        "dedupe_window_seconds": 86400,
        "handoff_ack": True,
        "ack_text": "передано",
    },
    "directory": {"refresh_seconds": 60, "profiles": []},
    "bus": {
        "poll_seconds": 2.0,
        "lease_seconds": 300,
        "max_attempts": 5,
        "backoff_seconds": [5, 30, 120, 600],
        "availability_poll_seconds": 60,
    },
    "ledger": {"state_path": "", "retention_done_days": 14},
    "limits": {"max_payload_bytes": 65536, "max_summary_chars": 4000},
    "transcription": {"provider": "none", "model": "whisper-1", "api_key_env": "", "language": ""},
}


def load_config(ctx) -> dict[str, dict[str, Any]]:
    """Merge operator settings (``plugins.entries.<id>.settings.*``) over defaults."""
    config: dict[str, dict[str, Any]] = {}
    for section, defaults in CONFIG_DEFAULTS.items():
        try:
            value = ctx.get_config(section, None) if ctx is not None else None
        except Exception:
            value = None
        if isinstance(value, dict):
            merged = dict(defaults)
            merged.update(value)
            config[section] = merged
        else:
            config[section] = dict(defaults)
    return config


class GatewayMessageSender:
    """Captures the gateway handle at hook time; sends side-channel chat messages.

    A profile only ever uses its own adapters: outbound identity stays per-profile
    and never borrows another profile's bot.
    """

    def __init__(self) -> None:
        self._gateway: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()

    def attach(self, gateway: Any) -> None:
        if gateway is None or gateway is self._gateway:
            return
        self._gateway = gateway
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = getattr(gateway, "_loop", None) or getattr(gateway, "loop", None)

    def _adapter_for(self, platform: str):
        adapters = getattr(self._gateway, "adapters", None)
        if not isinstance(adapters, dict) or not platform:
            return None
        direct = adapters.get(platform)
        if direct is not None:
            return direct
        for key, adapter in adapters.items():
            if str(getattr(key, "value", key)) == str(platform):
                return adapter
        return None

    @staticmethod
    def _thread_metadata(address: dict[str, Any]) -> dict[str, str] | None:
        """Hermes adapters read the topic from ``metadata['thread_id']``; without it a
        topic-bound message lands in the General topic."""
        thread_id = str(address.get("thread_id", "") or "")
        return {"thread_id": thread_id} if thread_id else None

    def send_from_hook(self, source: dict[str, Any], text: str) -> bool:
        """Fire-and-forget send while running inside the gateway loop (acks)."""
        adapter = self._adapter_for(str(source.get("platform", "")))
        if adapter is None:
            return False
        coroutine = adapter.send(
            str(source.get("chat_id", "")), text, metadata=self._thread_metadata(source)
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        task = loop.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def send_from_thread(self, destination: dict[str, Any], text: str) -> bool:
        """Blocking send from the consumer thread (mirror copies, notices)."""
        if self._loop is None or self._loop.is_closed():
            return False
        adapter = self._adapter_for(str(destination.get("platform", "")))
        if adapter is None:
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                adapter.send(
                    str(destination.get("chat_id", "")), text,
                    metadata=self._thread_metadata(destination),
                ),
                self._loop,
            )
            return bool(future.result(timeout=30))
        except Exception:
            logger.debug("side-channel send failed", exc_info=True)
            return False


class Components:
    """The wired object graph; also the seam tests use with fakes."""

    def __init__(self, *, profile: str, db: Database, ledger: Ledger, routes: RouteRegistry,
                 bus: MessageBus, directory: Directory, gate: TriggerGate, intake: RelayIntake,
                 consumer: ProfileConsumer, sender: GatewayMessageSender, config: dict[str, dict]) -> None:
        self.profile = profile
        self.db = db
        self.ledger = ledger
        self.routes = routes
        self.bus = bus
        self.directory = directory
        self.gate = gate
        self.intake = intake
        self.consumer = consumer
        self.sender = sender
        self.config = config


def build_components(*, profile: str, config: dict[str, dict[str, Any]], db: Database,
                     ack_scheduler=None, thread_ack_scheduler=None) -> Components:
    ledger = Ledger(db)
    routes = RouteRegistry(db)
    bus = MessageBus(
        db,
        lease_seconds=int(config["bus"]["lease_seconds"]),
        max_attempts=int(config["bus"]["max_attempts"]),
        backoff_seconds=list(config["bus"]["backoff_seconds"]),
        availability_poll_seconds=int(config["bus"]["availability_poll_seconds"]),
    )
    directory = Directory(
        db,
        roster=list(config["directory"].get("profiles") or []),
        refresh_seconds=int(config["directory"]["refresh_seconds"]),
    )
    gate = TriggerGate(
        ledger,
        ignore_forwarded_mentions=bool(config["relay"]["ignore_forwarded_mentions"]),
        max_hops=int(config["relay"]["max_hops"]),
    )
    sender = GatewayMessageSender()
    if ack_scheduler is None:
        ack_scheduler = sender.send_from_hook
    if thread_ack_scheduler is None:
        thread_ack_scheduler = sender.send_from_thread
    home = paths.hermes_home()
    audio_area = Path(home) / "plugin-data" / PLUGIN_NAME / "audio"
    provider = provider_from_config(config["transcription"])
    intake = RelayIntake(
        profile=profile,
        bus=bus,
        routes=routes,
        ledger=ledger,
        gate=gate,
        ack_scheduler=ack_scheduler,
        thread_ack_scheduler=thread_ack_scheduler,
        relay_enabled=bool(config["relay"]["enabled"]),
        max_hops=int(config["relay"]["max_hops"]),
        max_payload_bytes=int(config["limits"]["max_payload_bytes"]),
        audio_area=audio_area,
        transcription_provider=provider,
        ack_text=str(config["relay"]["ack_text"]),
    )
    consumer = ProfileConsumer(
        profile=profile,
        bus=bus,
        ledger=ledger,
        routes=routes,
        directory=directory,
        poll_seconds=float(config["bus"]["poll_seconds"]),
        max_payload_bytes=int(config["limits"]["max_payload_bytes"]),
        max_summary_chars=int(config["limits"]["max_summary_chars"]),
    )
    return Components(profile=profile, db=db, ledger=ledger, routes=routes, bus=bus,
                      directory=directory, gate=gate, intake=intake, consumer=consumer,
                      sender=sender, config=config)


def register_plugin(ctx) -> None:
    config = load_config(ctx)
    profile = paths.profile_name()
    state_value = str(config["ledger"].get("state_path") or "").strip()
    state_path = paths.expand_path(state_value) if state_value else paths.default_bus_state_path()
    db = Database(state_path)
    components = build_components(profile=profile, config=config, db=db)

    def on_pre_gateway_dispatch(event=None, gateway=None, session_store=None, **_):
        components.sender.attach(gateway)
        components.directory.heartbeat(profile)
        return components.intake.handle_event(event, gateway, session_store)

    def on_post_llm_call(**kwargs):
        components.consumer.on_turn_completed(**kwargs)

    try:
        ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)
        ctx.register_hook("post_llm_call", on_post_llm_call)
    except Exception:
        logger.exception("hook registration failed — plugin stays inert")

    injector_origin = None
    injector_keyless = None
    try:
        def injector_origin(origin, content):
            return bool(ctx.inject_message(content=content, role="user", origin=origin))

        def injector_keyless(content):
            return bool(ctx.inject_message(content=content, role="user"))
    except Exception:
        logger.exception("inject_message unavailable — agent delivery disabled in this profile")

    components.consumer.injector = injector_origin
    components.consumer.keyless_injector = injector_keyless
    components.consumer.display_sender = components.sender.send_from_thread
    components.consumer.start()

    handlers = build_tool_handlers(components)
    for name, schema, description, handler in handlers:
        try:
            ctx.register_tool(name, TOOLSET, schema, handler, description=description)
        except Exception:
            logger.exception("tool registration failed for %s", name)

    def _shutdown() -> None:
        """Release the shared database file and stop the consumer thread."""
        try:
            components.consumer.stop()
        except Exception:
            logger.debug("consumer stop failed", exc_info=True)
        try:
            components.db.close()
        except Exception:
            logger.debug("database close failed", exc_info=True)

    try:
        ctx.on_unload(_shutdown)
    except Exception:
        logger.exception("on_unload registration failed")

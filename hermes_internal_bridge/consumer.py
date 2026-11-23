"""Profile consumer (variant B): the HIB instance in each target profile.

Two loops run per profile:

* agent loop — claims agent-bound messages, injects them into the profile's own
  sessions via ``ctx.inject_message``; ``False`` means the target topic is not
  reachable yet (availability rule): the message stays ``queued`` with
  ``relay_target_unavailable``, and the source is notified exactly once;
* display loop — claims mirror copies and notices and sends them to a chat via
  the profile's own adapter identity (never borrows another profile's bot).

``on_turn_completed`` correlates a finished agent turn (``post_llm_call``) with
the delivered relay, marks it done, and — for mirror routes — enqueues the reply
copy back to the source profile through the bus.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .bus import MessageBus
from .envelope import Envelope, derived_relay_id, new_chat_message_envelope, relay_marker
from .errors import BridgeError
from .handoff import handoff_result_text, handoff_task_text

logger = logging.getLogger("hib.consumer")

Injector = Callable[..., bool]
DisplaySender = Callable[[dict[str, Any], str], bool]


class ProfileConsumer:
    """Delivers queued messages into this profile and mirrors replies back."""

    def __init__(
        self,
        *,
        profile: str,
        bus: MessageBus,
        ledger,
        routes=None,
        directory=None,
        injector: Injector | None = None,
        keyless_injector: Injector | None = None,
        display_sender: DisplaySender | None = None,
        poll_seconds: float = 2.0,
        max_payload_bytes: int = 65536,
        max_summary_chars: int = 4000,
    ) -> None:
        self.profile = profile
        self.bus = bus
        self.ledger = ledger
        self.routes = routes
        self.directory = directory
        self.injector = injector
        self.keyless_injector = keyless_injector
        self.display_sender = display_sender
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.max_payload_bytes = max_payload_bytes
        self.max_summary_chars = max_summary_chars
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_count = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name=f"hib-consumer-{self.profile}", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("consumer tick failed for profile %s", self.profile)
            self._stop_event.wait(self.poll_seconds)

    # ------------------------------------------------------------------ tick

    def tick(self) -> None:
        self._tick_count += 1
        self.bus.release_expired_leases()
        if self.directory is not None and self._tick_count % 10 == 0:
            self.directory.heartbeat(self.profile)
        row = self.bus.claim(self.profile)
        if row is not None:
            self.deliver_agent(row)
        display_row = self.bus.claim_display(self.profile)
        if display_row is not None:
            self.deliver_display(display_row)

    # ------------------------------------------------------------- agent leg

    def deliver_agent(self, row: dict[str, Any]) -> None:
        envelope = Envelope.from_json(row["envelope_json"])
        relay_id = envelope.relay_id
        try:
            if self.directory is not None and not self.directory.known(envelope.target_profile):
                # FR-024: unknown target → route blocked; no redirect to default.
                self._fail_unavailable(envelope, row, "profile_not_in_roster")
                return
            content = self._agent_content(envelope)
            self.bus.set_payload_text(relay_id, target_profile=self.profile, text=content)
            if envelope.kind == "handoff.request":
                self.bus.mark_accepted(relay_id, target_profile=self.profile, accepted_by=self.profile)
                self._ledger("handoff_accepted", relay_id, route_id=row.get("route_id", ""),
                             details={"accepted_by": self.profile})
            injected = self._inject(envelope, content)
        except Exception as exc:
            self._fail_processing(relay_id, row, str(exc))
            return
        if injected:
            self.bus.mark_delivered(relay_id, target_profile=self.profile)
            self._ledger("relay_delivered", relay_id, route_id=row.get("route_id", ""),
                         details={"target_chat": envelope.destination.get("chat_id", "")})
        else:
            # Availability rule: no human message ever reached the target topic —
            # inject_message returned False; the message stays queued (FR-006).
            self._fail_unavailable(envelope, row)

    def _inject(self, envelope: Envelope, content: str) -> bool:
        destination = envelope.destination
        has_chat = bool(str(destination.get("chat_id", "") or "").strip())
        if has_chat and self.injector is not None:
            origin = {
                "instance_id": "local",
                "profile": self.profile,
                "platform": str(destination.get("platform") or envelope.source.get("platform") or ""),
                "chat_id": destination.get("chat_id", ""),
                "thread_id": destination.get("thread_id", "") or "",
                "user_id": destination.get("user_id") or envelope.source.get("user_id") or "",
                # Hermes reads a missing chat_type as "dm", which changes the session key
                # (telegram:group:<chat> vs telegram:dm:<chat>) and the injection is refused
                # with "no human message admitted yet".
                "chat_type": self._origin_chat_type(destination, envelope),
            }
            return bool(self.injector(origin=origin, content=content))
        if self.keyless_injector is not None:
            return bool(self.keyless_injector(content=content))
        return False

    @staticmethod
    def _origin_chat_type(destination: dict, envelope: Envelope) -> str:
        explicit = str(destination.get("chat_type") or "").strip()
        if explicit:
            return explicit
        if str(destination.get("chat_id", "")).strip().startswith("-"):
            return "group"
        return str(envelope.source.get("chat_type") or "dm")

    def _agent_content(self, envelope: Envelope) -> str:
        header = relay_marker(envelope.relay_id, envelope.hop)
        if envelope.kind == "handoff.request":
            deadline = envelope.payload.get("deadline_at")
            deadline = float(deadline) if isinstance(deadline, (int, float)) else None
            body = handoff_task_text(
                str(envelope.payload.get("summary", "")),
                [str(c) for c in envelope.payload.get("constraints", [])],
                str(envelope.payload.get("expected_result", "") or ""),
                deadline,
            )
            return f"{header}\n{body}"
        return f"{header} {envelope.payload_text()}".strip()

    # ----------------------------------------------------------- display leg

    def deliver_display(self, row: dict[str, Any]) -> None:
        envelope = Envelope.from_json(row["envelope_json"])
        relay_id = envelope.relay_id
        text = self._display_text(envelope)
        try:
            if self.display_sender is None:
                raise BridgeError("relay_target_unavailable", "Отправка в чат недоступна в этом профиле.")
            sent = bool(self.display_sender(envelope.destination, text))
        except BridgeError as exc:
            self._fail_retryable(relay_id, row, exc.code)
            return
        except Exception as exc:
            self._fail_retryable(relay_id, row, str(exc))
            return
        if sent:
            self.bus.complete(relay_id, target_profile=self.profile, completed_by=self.profile)
            event_name = "relay_mirror_delivered" if envelope.relay_mirror else "notice_delivered"
            self._ledger(event_name, relay_id, route_id=self.bus.route_id_for(relay_id))
        else:
            self._fail_retryable(relay_id, row, "relay_target_unavailable")

    def _display_text(self, envelope: Envelope) -> str:
        if envelope.kind == "handoff.result":
            return handoff_result_text(envelope.payload)
        return envelope.payload_text()

    # ------------------------------------------------- turn completion hook

    def on_turn_completed(self, *, user_message: str = "", assistant_response: str = "", **_: Any) -> None:
        """Correlate a finished turn (``post_llm_call``) with the delivered relay."""
        if not user_message:
            return
        row = self.bus.find_for_reply(target_profile=self.profile, payload_text=user_message)
        if row is None:
            return
        relay_id = row["relay_id"]
        correlation_id = self.bus.correlation_id_for(relay_id)
        if not correlation_id:
            self._ledger("relay_correlation_missing", relay_id, route_id=row.get("route_id", ""))
            return
        try:
            original = Envelope.from_json(row["envelope_json"])
        except BridgeError:
            original = None
        # Child envelopes are enqueued before the parent is completed: if the process
        # dies in between, the retry re-enqueues the same deterministic ids (dedup)
        # instead of losing the reply.
        if original is not None:
            mode = self._route_mode(row.get("route_id", ""))
            if mode == "mirror":
                self._enqueue_mirror(original, assistant_response)
            elif original.kind == "handoff.request":
                self._enqueue_handoff_result(original, assistant_response)
        self.bus.complete(relay_id, target_profile=self.profile, completed_by=self.profile)
        self._ledger("relay_done", relay_id, route_id=row.get("route_id", ""),
                     details={"completed_by": self.profile})

    # --------------------------------------------------------------- helpers

    def _enqueue_mirror(self, original: Envelope, assistant_response: str) -> None:
        """Copy the reply into the source chat (FR-005) via the source profile's adapter."""
        if not isinstance(assistant_response, str) or not assistant_response.strip():
            return
        mirror = new_chat_message_envelope(
            relay_id=derived_relay_id(original.relay_id, "mirror"),
            correlation_id=original.correlation_id,
            source={
                "instance_id": "local",
                "profile": self.profile,
                "platform": original.source.get("platform", ""),
                "user_id": original.destination.get("user_id", ""),
                "chat_id": original.destination.get("chat_id", ""),
                "thread_id": original.destination.get("thread_id", ""),
                "message_id": "",
            },
            destination={
                "instance_id": "local",
                "profile": original.source_profile,
                "platform": str(original.source.get("platform", "") or ""),
                "chat_id": str(original.source.get("chat_id", "") or ""),
                "thread_id": str(original.source.get("thread_id", "") or ""),
            },
            text=assistant_response,
            relay_mirror=True,
        )
        try:
            mirror.validate(max_payload_bytes=self.max_payload_bytes)
            self.bus.enqueue(mirror)
        except BridgeError as exc:
            self._ledger(exc.code, mirror.relay_id, details={"error": exc.message})
            return
        self._ledger("relay_mirror_enqueued", mirror.relay_id, details={"origin_relay": original.relay_id})

    def _enqueue_handoff_result(self, original: Envelope, assistant_response: str) -> None:
        summary = (assistant_response or "").strip()[: self.max_summary_chars]
        result = Envelope(
            relay_id=derived_relay_id(original.relay_id, "result"),
            correlation_id=original.correlation_id,
            kind="handoff.result",
            source={
                "instance_id": "local",
                "profile": self.profile,
                "platform": "",
                "user_id": "",
                "chat_id": "",
                "thread_id": "",
                "message_id": "",
            },
            destination={
                "instance_id": "local",
                "profile": original.source_profile,
                "platform": str(original.source.get("platform", "") or ""),
                "chat_id": str(original.source.get("chat_id", "") or ""),
                "thread_id": str(original.source.get("thread_id", "") or ""),
            },
            payload={
                "text": "",
                "is_voice_transcript": False,
                "attachments": [],
                "summary": summary,
                "constraints": [],
                "expected_result": None,
                "deadline_at": None,
                "priority": "normal",
                "status": "completed" if assistant_response else "failed",
            },
        )
        try:
            result.validate(max_payload_bytes=self.max_payload_bytes)
            self.bus.enqueue(result)
        except BridgeError as exc:
            self._ledger(exc.code, result.relay_id, details={"error": exc.message})
            return
        self._ledger("handoff_result_enqueued", result.relay_id, details={"origin_relay": original.relay_id})

    def _fail_unavailable(self, envelope: Envelope, row: dict[str, Any], reason: str = "") -> None:
        relay_id = envelope.relay_id
        self.bus.fail(
            relay_id,
            target_profile=self.profile,
            error="relay_target_unavailable",
            counts_attempt=False,
        )
        self._ledger("relay_target_unavailable", relay_id, route_id=row.get("route_id", ""),
                     details={"reason": reason or "target_topic_unreachable"})
        if not row.get("source_notified"):
            self._enqueue_notice(envelope, "relay_target_unavailable")
            self.bus.mark_source_notified(relay_id, target_profile=self.profile)

    def _fail_processing(self, relay_id: str, row: dict[str, Any], error: str) -> None:
        try:
            self.bus.fail(relay_id, target_profile=self.profile, error=error, counts_attempt=True)
        except BridgeError as exc:
            if exc.code == "bus_dead_letter":
                self._ledger("bus_dead_letter", relay_id, route_id=row.get("route_id", ""),
                             details={"error": error})
                return
            raise
        self._ledger("relay_failed", relay_id, route_id=row.get("route_id", ""), details={"error": error})

    def _fail_retryable(self, relay_id: str, row: dict[str, Any], error: str) -> None:
        self._fail_processing(relay_id, row, error)

    def _enqueue_notice(self, original: Envelope, code: str) -> None:
        """Tell the source chat once that delivery failed (FR-006: exactly one error)."""
        notice = Envelope(
            relay_id=derived_relay_id(original.relay_id, "notice"),
            correlation_id=original.correlation_id,
            kind="health",
            source={
                "instance_id": "local",
                "profile": self.profile,
                "platform": "",
                "user_id": "",
                "chat_id": "",
                "thread_id": "",
                "message_id": "",
            },
            destination={
                "instance_id": "local",
                "profile": original.source_profile,
                "platform": str(original.source.get("platform", "") or ""),
                "chat_id": str(original.source.get("chat_id", "") or ""),
                "thread_id": str(original.source.get("thread_id", "") or ""),
            },
            payload={
                "text": f"Доставка не выполнена ({code}). Сообщение осталось в очереди.",
                "is_voice_transcript": False,
                "attachments": [],
                "summary": None,
                "constraints": [],
                "expected_result": None,
                "deadline_at": None,
                "priority": "high",
            },
        )
        try:
            notice.validate(max_payload_bytes=self.max_payload_bytes)
            self.bus.enqueue(notice)
        except BridgeError as exc:
            self._ledger(exc.code, notice.relay_id, details={"error": exc.message})

    def _route_mode(self, route_id: str) -> str:
        if self.routes is None or not route_id:
            return ""
        route = self.routes.get(route_id)
        return route.mode if route is not None else ""

    def _ledger(self, event: str, relay_id: str, *, route_id: str = "", details: dict | None = None) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.event(relay_id, event, route_id=route_id, details=details)
        except Exception:
            logger.debug("ledger write failed for %s", event, exc_info=True)

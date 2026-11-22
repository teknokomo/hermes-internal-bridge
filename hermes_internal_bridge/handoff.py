"""Context handoff (spec §7.6): summary instead of transcript, with limits."""

from __future__ import annotations

import json
import math
import time
from typing import Any

from .errors import BridgeError


def validate_handoff_request(payload: dict[str, Any], *, max_summary_chars: int) -> dict[str, Any]:
    """Validate a ``handoff.request`` payload (FR-033, FR-034, FR-035)."""
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise BridgeError("relay_payload_invalid", "handoff.request требует непустой summary.")
    if len(summary) > max_summary_chars:
        raise BridgeError(
            "relay_payload_invalid",
            f"summary длиннее лимита {max_summary_chars} символов — используйте резюме, а не транскрипт.",
        )
    constraints = payload.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        raise BridgeError("relay_payload_invalid", "constraints должен быть списком строк.")
    deadline = payload.get("deadline_at")
    if deadline is not None:
        payload = dict(payload)
        payload["deadline_at"] = _deadline_to_epoch(deadline)
    priority = payload.get("priority", "normal")
    if priority not in ("low", "normal", "high"):
        raise BridgeError("relay_payload_invalid", "Недопустимый приоритет.")
    expected = payload.get("expected_result")
    if expected is not None and not isinstance(expected, str):
        raise BridgeError("relay_payload_invalid", "expected_result должен быть строкой.")
    return payload


def validate_handoff_result(payload: dict[str, Any], *, max_summary_chars: int) -> dict[str, Any]:
    """Validate a ``handoff.result`` payload (FR-037)."""
    status = payload.get("status")
    if status not in ("accepted", "completed", "failed"):
        raise BridgeError("relay_payload_invalid", "handoff.result.status должен быть accepted|completed|failed.")
    summary = payload.get("summary")
    if summary is not None:
        if not isinstance(summary, str):
            raise BridgeError("relay_payload_invalid", "handoff.result.summary должен быть строкой.")
        if len(summary) > max_summary_chars:
            raise BridgeError("relay_payload_invalid", "handoff.result.summary превышает лимит.")
    return payload


def _finite(value: float) -> float:
    """A deadline must be a real moment: ``inf``/``nan`` would disable every timeout."""
    if not math.isfinite(value):
        raise BridgeError("relay_payload_invalid", "deadline_at должен быть конечным моментом времени.")
    return value


def _deadline_to_epoch(deadline: Any) -> float:
    if isinstance(deadline, bool):
        raise BridgeError("relay_payload_invalid", "deadline_at должен быть epoch-числом или ISO-строкой.")
    if isinstance(deadline, (int, float)):
        return _finite(float(deadline))
    if isinstance(deadline, str):
        text = deadline.strip()
        try:
            return _finite(float(text))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                import calendar

                parsed = time.strptime(text, fmt)
                return float(calendar.timegm(parsed))
            except ValueError:
                continue
        try:
            from datetime import datetime, timezone

            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return _finite(parsed.timestamp())
        except ValueError as exc:
            raise BridgeError("relay_payload_invalid", "deadline_at не разобран.") from exc
    raise BridgeError("relay_payload_invalid", "deadline_at должен быть epoch-числом или ISO-строкой.")


def handoff_task_text(envelope_summary: str, constraints: list[str], expected_result: str,
                      deadline_epoch: float | None) -> str:
    """Human/agent-readable rendering of a handoff request for the target profile."""
    lines = [f"Задача (handoff): {envelope_summary}"]
    if constraints:
        lines.append("Ограничения: " + "; ".join(constraints))
    if expected_result:
        lines.append(f"Ожидаемый результат: {expected_result}")
    if deadline_epoch:
        lines.append(f"Срок: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(deadline_epoch))}")
    return "\n".join(lines)


def handoff_result_text(payload: dict[str, Any]) -> str:
    """Human-readable rendering of a handoff result for the requesting profile's chat."""
    status = payload.get("status", "failed")
    summary = payload.get("summary") or ""
    text = f"Handoff завершён: {status}"
    if summary:
        text += f". {summary}"
    return text


def constraints_to_json(constraints: list[str]) -> str:
    return json.dumps(constraints, ensure_ascii=False)

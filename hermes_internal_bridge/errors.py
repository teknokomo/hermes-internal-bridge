"""HIB error codes (spec §10) and the BridgeError exception.

User-facing messages name the action, never internal paths or identifiers.
"""

from __future__ import annotations

DEFAULT_MESSAGES: dict[str, str] = {
    "relay_route_missing": "Для этого источника нет маршрута — доставка не выполнена.",
    "route_conflict": "Для этого источника конфликтуют два маршрута — нужно решение оператора.",
    "relay_target_unavailable": "Целевой профиль или чат недоступен — сообщение осталось в очереди.",
    "relay_hop_limit": "Превышен лимит пересылок — сообщение отклонено.",
    "relay_duplicate": "Повторная доставка уже зарегистрирована — второе сообщение не создаётся.",
    "relay_filtered_forwarded": "Пересланное или relay-сообщение отфильтровано — агент не запускался.",
    "relay_acl_denied": "Передача запрещена политикой доступа.",
    "relay_payload_invalid": "Сообщение пустое или некорректно оформлено.",
    "relay_payload_too_large": "Сообщение превышает допустимый размер.",
    "relay_transcript_failed": "Голосовое не удалось распознать — текст не отправлен.",
    "relay_correlation_missing": "Ответ не связан с исходным сообщением.",
    "bus_target_busy": "Получатель занят — повтор позже.",
    "bus_lease_expired": "Время обработки истекло — сообщение возвращено в очередь.",
    "bus_dead_letter": "Попытки исчерпаны — сообщение в мёртвой очереди, нужен оператор.",
    "artifact_not_found": "Артефакт по ссылке не найден.",
    "artifact_access_denied": "Нет доступа к артефакту — копия не создаётся.",
    "approval_not_supported": "Approval-конверты не поддерживаются в этой версии.",
    "federation_not_supported": "Удалённые инстансы не поддерживаются в этой версии.",
    "gate_degraded": "Внутренняя ошибка шлюза — обычное сообщение пропущено.",
}


class BridgeError(Exception):
    """A single HIB failure carrying one of the spec §10 codes."""

    def __init__(self, code: str, message: str | None = None) -> None:
        if code not in DEFAULT_MESSAGES:
            raise ValueError(f"unknown HIB error code: {code!r}")
        self.code = code
        self.message = message or DEFAULT_MESSAGES[code]
        super().__init__(f"{code}: {self.message}")

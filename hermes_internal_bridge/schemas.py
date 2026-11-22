"""JSON schemas for the hib_* plugin tools (spec FR-047, FR-049).

Input size limits are declared in the schemas so oversized calls are rejected
before any handler runs; handlers re-check limits themselves.
"""

from __future__ import annotations

_TEXT = {"type": "string", "maxLength": 60000}
_ID = {"type": "string", "maxLength": 128}

TOOL_SPEC_TABLE: list[tuple[str, dict, str]] = [
    (
        "hib_status",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "Статус шины HIB: очереди, активные маршруты, последние ошибки, число отфильтрованных сообщений.",
    ),
    (
        "hib_routes_list",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "Список маршрутов HIB с их специфичностью и режимом (handoff/mirror).",
    ),
    (
        "hib_relay_send",
        {
            "type": "object",
            "properties": {
                "target_profile": _ID,
                "chat_id": {"type": "string", "maxLength": 256},
                "thread_id": {"type": "string", "maxLength": 256},
                "text": _TEXT,
                "correlation_id": {"type": "string", "maxLength": 64},
                "platform": {"type": "string", "maxLength": 64},
                "user_id": {"type": "string", "maxLength": 256},
                "source_chat_id": {"type": "string", "maxLength": 256},
            },
            "required": ["target_profile", "text"],
            "additionalProperties": False,
        },
        "Отправить сообщение в целевой профиль через шину с проверкой политики. Один маршрут — один получатель.",
    ),
    (
        "hib_bus_send",
        {
            "type": "object",
            "properties": {
                "target_profile": _ID,
                "kind": {"type": "string", "enum": ["handoff.request", "task.request", "task.result", "artifact.ready", "health"]},
                "payload": {"type": "object"},
                "chat_id": {"type": "string", "maxLength": 256},
                "thread_id": {"type": "string", "maxLength": 256},
                "correlation_id": {"type": "string", "maxLength": 64},
            },
            "required": ["target_profile", "kind", "payload"],
            "additionalProperties": False,
        },
        "Отправить конверт шины (handoff.request с резюме, task, артефакт-ссылка). Байты и base64 запрещены.",
    ),
    (
        "hib_bus_read",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["queued", "delivered", "processing", "done", "failed", "dead"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        "Прочитать сообщения, адресованные этому профилю. Чужие профили недоступны.",
    ),
    (
        "hib_bus_ack",
        {
            "type": "object",
            "properties": {
                "relay_id": _ID,
                "status": {"type": "string", "enum": ["done", "failed"]},
                "error": {"type": "string", "maxLength": 500},
            },
            "required": ["relay_id"],
            "additionalProperties": False,
        },
        "Подтвердить обработку сообщения этого профиля (done) или отметить сбой (failed).",
    ),
    (
        "hib_directory",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "Каталог локальных профилей: роли, capabilities, статус online/busy/offline.",
    ),
]

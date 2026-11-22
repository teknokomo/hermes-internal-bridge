# Changelog

## 0.1.0 (2026-11-19)

Первый релиз. Scope по ТЗ `TZ-HIB-0.1.0-r2`:

- chat-relay: intake на `pre_gateway_dispatch`, триггер-гейт (forwarded/relay → skip), дедупликация `relay_id` на уровне БД, ack в исходный чат;
- route registry: детерминированный выбор destination по специфичности, `route_conflict`, `relay_route_missing` (fail-closed), журнал выбора маршрута в ledger;
- local bus: SQLite, состояния `queued/delivered/processing/done/failed/dead`, lease, retry, dead-letter, идемпотентность;
- directory: реестр профилей из конфига, heartbeat, статусы online/busy/offline;
- контекстный handoff: `handoff.request`/`handoff.result`, лимиты резюме и payload, `accepted_by`/`completed_by`;
- артефакты только ссылкой (`sha256`, `uri`), base64 и байты запрещены;
- транскрипция голосовых в исходном профиле (асинхронно), в шину — только транскрипт и ссылка;
- ledger с фильтром секретов до записи; `hib status/routes/relay/replay/peers/directory`;
- plugin tools: `hib_status`, `hib_routes_list`, `hib_relay_send`, `hib_bus_send`, `hib_bus_read`, `hib_bus_ack`, `hib_directory`;
- зарезервированные интерфейсы: approvals (0.2.0) и federation (0.3.0) — типы валидируются и отклоняются.

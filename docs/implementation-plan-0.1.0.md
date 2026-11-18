# План реализации hermes-internal-bridge 0.1.0

Вход: ТЗ `TZ-HIB-0.1.0-r2` (hash подтверждён), Этап 0 (`docs/stage0-reference-analysis.md`), RESEARCH (`docs/research-16-questions.md`).
Вердикт доставки: **вариант B** (экземпляр HIB в каждом целевом профиле + общая шина; C не проходит — `gateway/run_plugin_injection.py:262`, `gateway/profile_routing.py:1–4`), A — только ack/mirror.
Разрешение на реализацию получено (2026-11-19, правки ТЗ 1–4 и 6 приняты, правка 5 снята).

## Дополнения Омисы (2026-11-19) — внесены в дизайн

1. **Транскрипция голоса — в исходном профиле, асинхронно после `pre_gateway_dispatch`.** В шину попадают только транскрипт и ссылка на оригинал, скопированный в область HIB исходного профиля. Consumer целевого профиля аудио не читает и не транскрибирует.
2. **Отдельный тест:** доставка в целевую тему, где ещё не было ни одного человеческого сообщения. Если `inject_message` возвращает `False` — ack не утверждает доставку; статус `relay_target_unavailable`, сообщение остаётся `queued`.
3. **Резолв корневого Hermes home** для общей шины доказать кодом и тестом на два случая: профиль, отличный от default, и сам default. `get_hermes_home()` возвращает профильный home — недостаточно. Реализация: `hermes_constants.get_default_hermes_root()` (есть в v0.23.1, ~:264) + защитная деривация `<root>/profiles/<name> → root`; тесты на оба случая.

## Ограничения (от Омисы)

- Production — Linux, Hermes 0.23.1+custom; dev — Windows 0.23.2. **API только из 0.23.1** (проверено по тегу v0.23.1: `hermes_constants.get_default_hermes_root/get_hermes_home`, `VALID_HOOKS` c `pre_gateway_dispatch/post_gateway_admission/pre_transcription`, allowlist `hermes_cli/data/plugin_api.json` с `hermes_constants`, `hermes_cli.config`, `hermes_cli.plugin_session:session_client`, `gateway.platforms.base/event/helpers`, `hermes_cli.plugin_events`, `plugins.plugin_storage`). Тесты обязаны проходить на Linux (CI-план).
- Общая БД шины: только конверты и метаданные; памяти, credentials и `.env` там нет; 0700 на Linux, NTFS ACL на Windows; фильтрация выборок по `target_profile` покрыта тестом; DLQ и replay — только оператор.
- `platforms: [linux, windows]`, Linux приоритетный.
- Ручная приёмка с реальными чатами — не на dev-машине; `deploy/README.md` с шагами оператора.
- Публикация: репозиторий `teknokomo/hermes-internal-bridge` (имя подтверждает владелец); коммиты от имени Владимира Левадного, без упоминания агентов; push — только по решению владельца.
- Готово = pytest зелёный (число тестов в отчёте), `plugins validate --json` passed, `plugins doctor --ci` exit 0, сканер секретов по ledger/логам — 0 совпадений, отчёт `NOT TOUCHED`.

## Ключевые дизайн-решения (нужны утверждения)

1. **Шина общая, не профильная.** Профиль Hermes — отдельный `HERMES_HOME`; `${HERMES_HOME}/plugin-data/…` из §9.1 ТЗ дал бы каждой копии HIB свою очередь. Для варианта B шина и маршруты живут в **корневом** Hermes home (Windows: `%LOCALAPPDATA%\hermes\plugin-data\hermes-internal-bridge\hib.sqlite3`), корень резолвится через allowlisted `hermes_constants`. Профильные данные HIB (heartbeats и т.п.) — в профильном `plugin-data`. *(правка ТЗ №3)*
2. **Маршруты — в общей SQLite (`hib_routes`), CLI оператора — источник изменений** (FR-018). `config_schema.routes` из §9.1 остаётся как альтернативный источник для одиночного профиля, но канонический путь — `hib routes …`. Один маршрут — один destination, conflict-детекция по специфичности (веса как в ядре: user 16 / thread 8 / chat 4 / platform-точное — согласовать с FR-015).
3. **Gate fail-closed внутри колбэка.** Фреймворк fail-open на исключении (`run_inbound_hooks.py:97–99`), поэтому вся логика gate — под собственным `try/except`: relay-метаданные → всегда `skip`; обычные сообщения при ошибке → `allow` + `gate_degraded`.
4. **Транскрипция голоса в consumer**, не в хуке (ядро транскрибирует на turn, `run_inbound.py:1827`; его модуль не allowlisted). Аудио — ссылкой.
5. **Directory — деградация до конфига** (roster-API не в allowlist, см. Q4): данные из конфига HIB + `hermes_cli.config`; полный roster — операторский `hib directory` (subprocess `hermes profile list`, официальный CLI). Решение владельца — по результату эксперимента с `hermes_state`.
6. **Windows-специфика:** `platforms: [windows, linux]`; «0700» → NTFS ACL (текущий пользователь) + POSIX 0700 на Linux; никаких systemd.

## Этапы

**R0. Скелет репозитория** — `git init D:\VSC\hermes-internal-bridge`; структура по Этапу 0: `plugin.yaml` (manifest v2, `requires_hermes: ">=0.23.1"`, `kind: standalone`, `provides_tools` ×7, `config_schema` §9.1), `__init__.py` c `register(ctx)`, `hermes_internal_bridge/`, `pyproject.toml` (setuptools, `requires-python >=3.11`, data-files `share/hermes/plugins/hermes-internal-bridge/…`), `MANIFEST.in`+`MANIFEST.sha256`, пары EN/RU (README, LICENSE), `SECURITY.md`, `CHANGELOG.md`, `AGENTS.md`, `CLAUDE.md`, `config/*.example.*`, `tests/`, `deploy/README.md` (Windows-операторские шаги), `docs/`. uv venv + `pytest`, `ruff`, `[tool.pytest.ini_options]`.

**R1. Ядро данных** — `envelope.py` (валидация §9.2: kind, `hop≤1`, ttl, relay/correlation/causation id), `errors.py` (коды §10), `db.py` (схема §9.3 + `schema_migrations`, WAL, короткие транзакции, ACL-установка директории). Тесты: `test_envelope.py`, `test_errors.py`, `test_db.py`.

**R2. Trigger gate** — `gate.py`: хук `pre_gateway_dispatch`; detect relay-метаданных (`relay_id`, `forward_origin`), `ignore_forwarded_mentions` (default true), `relay_hop_limit`, дедупликация `relay_id` — UNIQUE-индекс в БД (FR-011), fail-политика из дизайн-решения 3. Тесты: `test_gate.py` (§14.1: forwarded+mention→skip; обычный mention→allow; ошибка→`gate_degraded`; `hop>1`→отказ; duplicate→`relay_duplicate`).

**R3. Route registry + policy** — `routes.py`: CRUD в `hib_routes`, специфичность и конфликт (`route_conflict`), `relay_route_missing` (fail-closed, без default), журнал выбора маршрута в ledger (FR-019). Тесты: `test_routes.py`.

**R4. Local bus** — `bus.py`: состояния §5.1, lease (`bus.lease_seconds`), backoff (`bus.backoff_seconds`), dead-letter только оператору (FR-031), идемпотентный consumer на профиль, доставка через `ctx.inject_message` в свой профиль, ack через `gateway`-adapter send (вариант A как отображение). Тесты: `test_bus.py` (§14.1 переходы/lease/retry/DLQ; §14.2 рестарт).

**R5. Directory** — `directory.py` (дизайн-решение 5): roster-записи, capabilities, статус `online|busy|offline`, heartbeat, `blocked` для отсутствующих профилей (FR-024). Тесты: `test_directory.py`.

**R6. Ledger + фильтр секретов** — `ledger.py`: события §10, `route_id`, attempts; `secrets.py` — фильтр до записи (токены/ключи/.env-паттерны), ретеншн (сообщения `done` — по сроку, ledger дольше). Тесты: `test_ledger.py`, `test_secrets_filter.py` (сканер — ноль совпадений).

**R7. Handoff + артефакты** — `handoff.py`: `handoff.request`/`handoff.result` (§7.6), лимиты `max_summary_chars`/`max_payload_bytes` → `relay_payload_too_large`, `accepted_by`/`completed_by`; `artifacts.py`: только ссылки (FR-038–041), base64-запрет, `relay_acl_denied` для секретов. Тесты: `test_handoff.py`, `test_artifacts.py`.

**R8. CLI + plugin tools** — CLI-точка входа `hib` (pyproject `[project.scripts]`): `status/routes list|enable|disable/relay show/replay/peers/directory` (FR-046; `peers` — заготовка 0.3.0). Tools (FR-047): `hib_status`, `hib_routes_list`, `hib_relay_send`, `hib_bus_send`, `hib_bus_read`, `hib_bus_ack`, `hib_directory` — схемы с лимитами, коды §10 без трассировок (FR-049), без обхода ACL/gate (FR-048). Зарезервированные типы `approval.request`/`approval.decision` и `instance_id != local` — отклонение `approval_not_supported`/`federation_not_supported` (FR-050/051, fail-closed). Тесты: `test_tools.py`, `test_cli.py`.

**R9. Интеграция (фейки, без сети)** — по §14.2: DM→route→target (ровно одно сообщение + один ack), mirror без петли, A→B корреляция по `correlation_id`, рестарт gateway (queued доставляется), недоступный target (без потери и без default), конфликт маршрутов. Тестовый профиль: `hermes profile create hib-test`, установка плагина `--no-enable`/`adopt`, локальный `hermes gateway run` без платформ.

**R10. Приёмка** — `pytest` (число тестов в отчёте), `hermes plugins validate D:\VSC\hermes-internal-bridge --json` → passed, `hermes plugins doctor D:\VSC\hermes-internal-bridge --ci` → tools зарегистрированы, сканер секретов по дампу ledger/логов — 0 совпадений; инструкции ручной приёмки §14.3 — `deploy/README.md` для Омисы/оператора; отчёт `NOT TOUCHED`.

## Требует операторских прав / решений владельца

- Установка/обновление ПО на машине (выполнено в Этапе 1).
- Создание тестовых чатов и токенов ботов — у Омисы в основном Hermes (не на этой машине); выдача токенов — оператор.
- `hermes profile create hib-test` — оператор запускает (или даёт команду агенту).
- Включение плагина в целевых профилях и `plugins.entries.hermes-internal-bridge.allow_gateway_injection: true` — оператор.
- Включение маршрутов (`hib routes enable …`) — только оператор, никогда — код.
- Реплей/DLQ (`hib replay`, разбор dead-letter) — оператор.
- Решение о публикации репозитория и релизе — владелец.

## Критерии готовности (из §14.4, с уточнениями RESEARCH)

1. `pytest` — все тесты проходят, число указано.
2. `hermes plugins validate <path>` — passed; `hermes plugins doctor <path> --ci` — exit 0, tools зарегистрированы (подкоманда существует — проверено).
3. Импорты только из public-api allowlist (`hermes_cli/data/plugin_api.json`).
4. Секреты: сканер по ledger/логам — 0 совпадений.
5. DM relay — ровно одно сообщение и один ack; forwarded mention — 0 запусков агента.
6. Рестарт gateway — без потери `queued`.
7. Handoff — в лимитах, без полного транскрипта.
8. Native `profile_routes`, `multiplex_profiles`, Bot Mode, A2A, Kanban, `hermes-multica-bridge` — не изменены.
9. Отчёт `NOT TOUCHED: …` приложен.

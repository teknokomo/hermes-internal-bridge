# RESEARCH §16 — ответы на вопросы 2–6 по исходникам установленного Hermes

**Версия:** Hermes Agent v0.23.2, install dir `C:\Users\admin\AppData\Local\hermes\hermes-agent`, checkout `ae2a81ccc0` (2026-11-16, стабильный релиз; удовлетворяет `requires_hermes >=0.23.1`).
**Платформа:** Windows 10 22H2; `HERMES_HOME` на Windows — `%LOCALAPPDATA%\hermes` (подтверждено инсталлером: «config prepared in C:\Users\admin\AppData\Local\hermes»).
Вопрос 1 не перепроверялся (закрыт в ТЗ). Отметка: в 0.23.2 строки сместились — `hermes_cli/plugins.py:113` (`VALID_HOOKS`), `:142–145` (комментарий и запись `pre_gateway_dispatch`, kwargs `event/gateway/session_store`, семантика skip/rewrite/allow).

---

## Q2. Доставка в тему другого профиля без чтения его credentials

**Ответ: напрямую — нельзя; вариант C (§6.4) не проходит, доставка — вариант B.**

Доказательства:

- `ctx.inject_message(content, role="user", *, session_key=None, origin=None)` — `hermes_cli/plugins_context.py:397`. Требует `plugins.entries.<id>.allow_gateway_injection: true` — `plugins_context.py:441` (проверка `is True`); текст-подсказка — `hermes_cli/plugins_injection.py:38`.
- Адресат всегда внутри профиля плагина: `plugins_injection.py:61,63,65` — во всех трёх ветках `target` несёт `"plugin_home": manager.home_path` (точное совпадение с цитатами ТЗ); комментарий `:55–57`: «The manager's immutable home is the plugin's profile: the gateway creates an origin's session there or nowhere…».
- Кросс-профильный отказ: `gateway/run_plugin_injection.py:262` — `_plugin_session_key_admissible`: «Synchronous refusal of a keyed injection into another profile's session (local or messaging), so the plugin gets `False`»; `:276` — `_plugin_messaging_session_owned`: «A messaging session belongs to the profile its key names (`agent:<profile>:…`) under multiplexing … it must be the plugin's own»; `:290–293` — warning «belongs to profile %s, not the plugin's profile %s» → `False`.
- Origin-путь профиль не выбирает: `run_plugin_injection.py:6–7` — «The origin path never chooses a profile: the gateway's routing for that chat through that profile's adapter must already land in the plugin's profile».
- `gateway.profile_routes` — статический inbound-роутинг, программного доступа из плагина нет: `gateway/profile_routing.py:1–4` — «Matching is conjunctive; the highest additive specificity wins (`gateway.profile_routes` in config.yaml): user_id 16, thread_id 8, chat_id 4, guild_id 2, else the default profile». Роутинг срабатывает по метаданным входящего сообщения, а не по решению плагина.

**Следствие для дизайна:** вариант C применим только к inbound-роутингу исходного чата (какой профиль обслуживает DM), но не к доставке составленного сообщения в другой профиль. Доставка = **B**: HIB установлен в каждом целевом профиле (плагин включён в `plugins.enabled` профиля), общий SQLite-bus на корневом Hermes home, consumer в профиле-получателе делает `inject_message` в **свой** профиль (origin/session_key своего профиля). **A** (`adapter.send()`) — только отображение ack/mirror: бот не получает собственные сообщения как update, хода агента нет.

## Q3. Поведение injection для чужого профиля и чужой темы

- Чужой `session_key` → синхронный `False` (см. `run_plugin_injection.py:262,290–293`); авторизация перепроверяется на dispatch.
- Неизвестный ключ — «an unknown key is decided at dispatch» (`:262`).
- Idle-сессия → начинается turn; busy → сообщение ставится в очередь (docs; в 0.1.0 HIB идемпотентен по `relay_id`, очередь BUS — собственная).
- **Эксперимент при реализации** (не гадать): поведение `inject_message` с origin при незнакомом чате в тестовом профиле `hib-test`; ожидаемое — сессия создаётся в профиле плагина или `False`.

## Q4. Официальный roster профилей и capabilities

- Официальный API существует: `hermes_cli/profiles.py:990` — `list_profiles(*, lazy_skill_count=False) -> list[ProfileInfo]`, `:504` — `list_profile_names() -> list[str]`, `:484` — `_iter_named_profile_dirs(*, live_only=True)`; CLI — `hermes_cli/profile_cmd.py:618` (`cmd_profile`; команды `hermes profile list/show/...`).
- **Но:** плагинный import-allowlist (`hermes_cli/data/plugin_api.json`, 12 групп) **не содержит** `hermes_cli.profiles` ни в каком виде. Содержимое групп: `agent.*`, `cli`, `gateway.config`, `gateway.platform_registry`, `gateway.platforms.base/_shared/event/helpers`, `gateway.status`, `hermes_cli.auth_oauth_pkce_plugin`, `hermes_cli.config`, `hermes_cli.plugin_events`, `hermes_cli.plugin_provider_requests`, `hermes_constants`, `hermes_platform`, `hermes_state`, `plugins.plugin_storage`, `plugins.plugin_utils`, `pm`, `providers.*`, `tools.*`.
- **Следствие:** in-process plugin tool не может легально импортировать roster-API (`hermes plugins validate` предупреждает об импортах вне public surface — каталог-правило). Деградация по ТЗ: directory 0.1.0 строится из (a) конфига HIB и конфига собственного профиля через allowlisted `hermes_cli.config`, (b) записи маршрутов; полный roster — операторский CLI `hib directory`, который вне plugin-sandbox может вызывать `hermes profile list` (subprocess) — это официальный CLI, `state.db`/`.env` не читаются (FR-021 соблюдён).
- **Эксперимент при реализации:** проверить, отдаёт ли allowlisted `hermes_state`/`gateway.status` профильные данные; если нет — решение владельца о деградации (предусмотрено §16 Q4).

## Q5. Граница HIB bus и `message_agent` / `hermes peer` / Kanban

- **`message_agent`** — `tools/bot_mode_dm.py:40` (`MESSAGE_AGENT_TOOL_NAME = "message_agent"`): Bot Mode DM-цепочка между ботами, будит аскера каждым ответом (release 0.23.0), anti-loop `max_hops` (по умолчанию 4). Это agent-to-agent turn, а не транспорт с политикой — HIB bus не дублирует.
- **`hermes peer`** — `hermes_cli/subcommands/peer.py:465` (`{"dm","run","status","stop"}`), `:539` («Message an agent on a peer gateway and print its reply»), `web_routers/peer.py` — HTTP-роутер: machine-to-machine через API server, ориентир для federation 0.3.0 (FR-051: `federation_not_supported` в 0.1.0).
- **Kanban** — `tools/kanban_tools.py:154` (`_kanban_handler`) + `tools/kanban_tools_schemas.py`: durable-задачи с review; `gateway/kanban_watchers.py:152–155` — `_deliver_kanban_artifacts` («Upload artifact files referenced by a completed kanban task»).
- **Следствие:** FR-030 подтверждён — HIB bus = at-most-once транспорт c lease/retry/DLQ и политикой маршрутов, без wake-петли; задачи с review остаются в Kanban, межботовские ходы — в message_agent, межинстансное — в `hermes peer` (0.3.0).

## Q6. Хранение вложений без копирования секретов

- **Document store отсутствует**: grep `document_store|DocumentStore` по `gateway/`, `tools/`, `agent/` — 0 совпадений.
- Артефакты в Hermes существуют только в Kanban-доставке (`gateway/kanban_watchers.py:152–155`); медиа входящих — `gateway/media_fetch.py`, кэш — `%LOCALAPPDATA%\hermes\audio_cache` и аналогичные.
- **Следствие для 0.1.0:** FR-038–041 реализуются как «ссылка»: `{artifact_id, kind, size_bytes, sha256, uri, owner_profile}`; `uri` = путь внутри `HERMES_HOME`/платформенный `file_id`; байты и base64 запрещены; доступ проверяет получатель своими credentials (`artifact_access_denied`). Поле `document_id` резервируется под 0.2.0.

## Гипотеза доставки §6.4 — вердикт

**B проходит, C — нет** (правило ТЗ: C если проходит, иначе B). Доказательства — Q2. A — только ack/mirror-отображение. Требуемая операторская настройка для B: `plugins.entries.hermes-internal-bridge.allow_gateway_injection: true` в целевых профилях (это конфиг плагина, не core-патч).

## Модель отказа gate (уточнение для FR-013)

`gateway/run_inbound_hooks.py`:
- `:80–86` — семантика: `{"action":"skip"}` → drop; `{"action":"rewrite","text"}` → заменить `event.text`; `allow`/`None` → обычный dispatch.
- `:90–95` — вызов `ainvoke_hook("pre_gateway_dispatch", event=event, gateway=self, session_store=…)` — kwargs подтверждены.
- `:97–99` — `except Exception` → `logger.warning("pre_gateway_dispatch invocation failed: …")` + `_hook_results = []` → **fail-open на уровне фреймворка**: исключение колбэка не останавливает dispatch.
- `:101–111` — первый распознанный dict выигрывает; `skip` → `return None` (дроп).

**Следствие:** гарантия «relay никогда не доходит до агента» достижима только внутри колбэка HIB: полный `try/except` вокруг всей логики; при внутренней ошибке сообщения с relay-метаданными всё равно получают `skip` (fail-closed), обычные — `allow` + `gate_degraded` в ledger.

## Транскрипция голоса (FR-002)

- `pre_gateway_dispatch` работает на ingress ДО auth: `gateway/run_inbound.py:223` (`event = await self._hm_pre_gateway_dispatch_hook(event, source)`), а транскрипция выполняется позже, на исполнении turn: `run_inbound.py:1827` (`_transcribe_pending_audio_event_once`).
- Ядро транскрипции — `gateway/run_inbound_transcription.py:1–23` — **не в плагинном allowlist**.
- **Следствие:** HIB транскрибирует самостоятельно, в consumer (вне 50 мс-бюджета хука, §12 ТЗ); путь/attachment берётся из события. Эксперимент при реализации: убедиться, что audio-paths доступны на event в момент хука (в тестовом профиле, без реальных платформ — фейковый адаптер).

## Дополнительно: проверка критериев приёмки §14.4

- `hermes plugins validate <path>` — существует: `usage: hermes plugins validate [-h] [--install-deps] [--json] path` («Validate a plugin directory for catalog admission (CI gate)»).
- `hermes plugins doctor [target] [--ci]` — **существует** (доксайт не упоминал): `usage: hermes plugins doctor [-h] [--ci] [target]`; `--ci` — «Exit non-zero when validation reports an error». Правка ТЗ по этому пункту не нужна.
- Установка локального плагина для теста: `hermes plugins install <git-url> --ref <sha> --no-enable` или `hermes plugins adopt <name>` («For plugin dirs you cloned yourself … become a tracked git install»).

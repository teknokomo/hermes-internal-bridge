# Этап 0. Анализ референс-репозитория (§13 ТЗ)

**Референс:** https://github.com/teknokomo/hermes-multica-bridge, ветка `main`
**Клон:** `D:\VSC\hermes-multica-bridge` (только чтение, изменений не вносилось)
**Зафиксированный HEAD:** `c7f06cfb736e1cd2fb2e0288640be17f49c705b2` (`c7f06cf`, 2026-11-17, «Merge pull request #23: 0.7.0 runtime routing and approval wake»)
Все ссылки `файл:строка` даны по этому коммиту.

## 13.1. Взять как образец

| Что | Где смотреть | Зачем / что именно |
|---|---|---|
| Манифест плагина v2 | `plugin.yaml:3` (`manifest_version: 2`), `:4` (`requires_hermes: ">=0.22.4"` — плоская semver-строка), `:7–10` (`license`, `kind: standalone`, `platforms`), `:11–25` (`provides_tools`, 14 tools), `:26–29` (`tags`), `:30–63` (`config_schema`) | Полный скелет манифеста для `hermes-internal-bridge` |
| Typed config_schema с `${HERMES_HOME}` | `plugin.yaml:30–63`: записи `registry` (:31–40), `runtime_router` (:41–56), `condition_wake` (:57–63); каждая — `type: dict`, `label`, `description`, `default`; путь состояния `:47` — `"${HERMES_HOME}/plugin-data/hermes-multica-bridge/runtime-router.sqlite3"` | Шаблон конфигурации §9.1 ТЗ; RU-описания внутри `config_schema` при EN-`description` — конвенция репозитория |
| Упаковка | `pyproject.toml:1–3` (setuptools>=77 / build_meta), `:5–13` (метаданные, `requires-python: ">=3.11"`, `license-files: ["LICENSE-RU.md"]`), `:15–17` (`[project.scripts]`), `:19–23` (`packages.find` include), `:25–36` (`data-files` → `share/hermes/plugins/<name>/…`), `:38–47` (ruff per-file-ignores, `exclude = [".agents"]`) | Воспроизводимая сборка; обратите внимание: `[tool.pytest.ini_options]` в референсе НЕТ (grep `tool.pytest` — 0 совпадений) — pytest-конвенция живёт только в структуре `tests/`; для HIB pytest-конфиг добавить явно |
| Контроль целостности | `MANIFEST.in:1–22` (include списки; `prune tests/memory-bank/.agents/.backup/.claude/deploy/tools` — :16–20, :22; `global-exclude __pycache__ *.py[cod] *.sqlite3 *.sqlite3-* .env .env.* *.log` — :21), `MANIFEST.sha256` (top-level) | Упаковка без dev-каталогов и без секретов/состояния |
| Тесты | `tests/` — 38 записей: 36 pytest-файлов + `fixtures/` + `helpers.py` + `__init__.py`. Репрезентативные: `test_validation.py`, `test_registry.py`, `test_dispatcher.py`, `test_routes.py`, `test_notification_policy.py`, `test_state.py`, `test_configuration.py`; Node-тесты в том же каталоге: `test_desktop_profile_guard.mjs`, `test_desktop_registry_ui.mjs` (для HIB 0.1.0 не нужны) | Структура pytest-набора, изоляция состояния, негативные сценарии |
| Примеры конфигурации | `config/` — 8 файлов `*.example.{json,toml}`: `routes.example.toml`, `notify.example.toml`, `registry.om73.example.json`, `runtime-router.example.json`, `wake-webhook-policy.example.json`, `watcher.example.toml`, `watcher-bindings.example.toml`, `multica-watch.example.json` | По одному примеру на подсистему, без секретов |
| Операторские шаги | `deploy/README.md` — RU, нумерованные разделы: «1. Установить файлы» (:60), «2. Подготовить Isolated instance» (:114), «3. Проверить generated files» (:144), «4. Проверить unit files» (:168), «5. Активировать instance» (:176), «6. Активировать Isolated Route в Hermes profile» (:206); «Ротация webhook/HMAC/PAT» (:226), «Немедленная остановка» (:246), «Live проверка» (:276) | Формат деплой-документации без секретов; для HIB — адаптировать под Windows/in-process (без systemd) |
| Двуязычная документация | `README.md:1` (`🇬🇧 **English** \| 🇷🇺 [Русский](README-RU.md)`), пары `README.md`+`README-RU.md`, `LICENSE.md`+`LICENSE-RU.md`; `SECURITY.md`, `CHANGELOG.md` — только EN | Конвенция пары EN/RU |
| Инструкции для агентов | `AGENTS.md`, `CLAUDE.md` (top-level), `.agents/skills/` (autoreview, generated, …), `.claude/skills/ontoindex/*` (8 SKILL.md) | Правила работы исполнителей в репозитории |
| Собственный skill плагина | `skills/hermes-multica-bridge/SKILL.md`; ставится в дистрибутив через `pyproject.toml:36` | Плагин может поставлять Hermes-skill вместе с tools — задел для HIB (CLI-справка `hib`) |

## 13.2. Не переносить

| Что | Где в референсе | Причина |
|---|---|---|
| Multica-registry (`connections/workspaces/routes/operator_grants/route_grants`) | `bridge/registry.py:170` (`RegistryConnection`), `:186` (`RegistryWorkspace`), `:201` (`RegistryRoute`), `:259` (`OperatorGrant`), `:266` (`RouteGrant`), `:308` (`ConnectionRegistry`), `:1010` (`registry_from_mapping`); `plugin.yaml:31–40` (`config_schema.registry`); `tests/test_registry.py` | Multica-специфика; у HIB другая предметная область (routes/bus/ledger §9.3 ТЗ) |
| Condition wake обратной доставки | `bridge/condition_wake.py:1` (validation helpers для двухфазных wakeups), `plugin.yaml:57–63` (`config_schema.condition_wake`, `native_delivery_suppressed`), `notify/wake.py`, `tests/test_condition_wake.py`, `tests/test_notify_wake.py` | HIB не следит за задачами Multica. **Расхождение с ТЗ §13.2:** механизм в референсе называется `condition_wake`; термин «completion wake» встречается только в EN-описании манифеста (`plugin.yaml:5`) |
| Watcher обратной доставки | `bridge/watcher.py`, `watcher_api.py`, `watcher_config.py`, `watcher_delivery.py`, `watcher_ipc.py`, `watcher_state.py` (6 модулей); `config/watcher.example.toml`, `config/multica-watch.example.json` | HIB не следит за Multica; у HIB свой consumer-цикл на шине |
| `runtime_router` | `runtime_router/` — 9 файлов (`cli.py:528` — `def main`); `tests/test_runtime_router.py`; `config/runtime-router.example.json`; `plugin.yaml:41–56` | Переключение canonical agent — отдельная ответственность; HIB не выбирает runtime |
| Webhook-политика и её коды | `bridge/webhook.py:68–79, :454–456` (`invalid_request`, `request_too_large`, …), `:397–409` (`duplicate_without_run`, `delivery_skipped`, `delivery_ignored`) | Другой транспорт и контракт; коды ошибок HIB — §10 ТЗ |
| Dashboard/desktop и Node-тесты | `dashboard/` (`manifest.json`, `plugin_api.py`, `dist/`), `desktop/*.js/*.mjs`, `tests/test_desktop_*.mjs` | Вне scope 0.1.0 (UI-подсистемы нет) |
| systemd-юниты | `deploy/systemd/*.service|socket|timer` (`pyproject.toml:35`, `MANIFEST.in:10`) | HIB: consumer in-process, dev на Windows; операторские шаги — документацией, не unit-файлами |
| Реальные ID/секреты примеров | `config/registry.om73.example.json` и соседние | В HIB только плейсхолдеры (§11 ТЗ) |

## 13.3. Что придётся адаптировать (другая модель состояния)

1. **Платформы:** референс — `platforms: [linux]` (`plugin.yaml:9–10`); HIB разрабатывается на Windows → `platforms: [windows, linux]` + NTFS ACL вместо POSIX 0700 (правка ТЗ предложена отдельно).
2. **Общая шина между профилями:** в референсе state_path — один плагин в одном профиле (`plugin.yaml:47`). Для варианта B (§6.4 ТЗ) шина HIB должна быть общей для всех профилей хоста: `${HERMES_ROOT}/plugin-data/...` (корневой Hermes home, не профильный `${HERMES_HOME}`; на Windows корень — `%LOCALAPPDATA%\hermes`, константа доступна через allowlisted `hermes_constants`).
3. **pytest-конфиг:** в референсе отсутствует — для HIB добавить `[tool.pytest.ini_options]` явно.
4. **RU-описания в config_schema** (`plugin.yaml:34,44,60`) при EN-верхушке — конвенцию сохранить.
5. **prune-список MANIFEST.in** адаптировать: без `memory-bank`/`deploy/tools` (у HIB другие вспомогательные каталоги), сохранить prune `.agents/.claude/tests` и global-exclude `*.sqlite3* .env* *.log` (`MANIFEST.in:16–21`).

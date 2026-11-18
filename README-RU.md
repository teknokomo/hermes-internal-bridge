🇬🇧 [English](README.md) | 🇷🇺 **Русский**

# Hermes Internal Bridge (HIB) 0.1.0

Внутренний мост между профилями Hermes: релей личных чатов с триггер-гейтом, детерминированная политика маршрутов, durable-шина сообщений, контекстный handoff, артефакты по ссылке и аудит-журнал.

HIB — umbrella-плагин Hermes. Он не заменяет Hermes core, не строит второй A2A и не подменяет Kanban. Его роль: **политика, маршрутизация, конверт и аудит** поверх native-механизмов.

## Модули (0.1.0)

| Модуль | Назначение |
|---|---|
| `chat-relay` | личный чат Telegram/MAX → профильная тема/чат |
| `directory` | реестр локальных профилей, ролей, capabilities, health |
| `profile-bus` | durable-сообщения и задачи между профилями |
| `ledger` | аудит, корреляция, retry, dead-letter |
| `policy` | маршруты, ACL, лимиты |
| `cli` | `hib status/routes/relay/replay/peers/directory` |
| `approvals` | зарезервированный интерфейс (реализация в 0.2.0) |
| `federation` | зарезервированный интерфейс (реализация в 0.3.0) |

## Принципы

- Один маршрут — один destination. Без fan-out.
- Никакого native forward. Только сгенерированное сообщение с relay-метаданными.
- Упоминание не является триггером для relay/forwarded.
- Один hop. Relay-сообщение нельзя переслать дальше.
- Сначала policy, потом LLM. Маршрут выбирает конфигурация.
- Секреты не пересылаются.
- Профили остаются изолированными. Передаются резюме, ссылки, артефакты — не память и не credentials.
- Fail-closed на неопределённости. Нет маршрута — отказ, а не «ближайший профиль».

## Установка (оператор)

Шаги оператора — в [deploy/README.md](deploy/README.md). Кратко:

1. Установить Hermes Agent 0.23.1+ (`requires_hermes: ">=0.23.1"`).
2. Установить плагин в целевые профили: `hermes plugins install teknokomo/hermes-internal-bridge --no-enable` (или adopt склонированного каталога).
3. В каждом целевом профиле включить `plugins.entries.hermes-internal-bridge.allow_gateway_injection: true`.
4. Создать маршруты через CLI `hib routes`. Маршруты включает только оператор.
5. Провести ручную приёмку с реальными чатами (`deploy/README.md`, финальный раздел).

## Разработка

```bash
uv venv
uv pip install pytest ruff
uv run pytest
hermes plugins validate . --json
hermes plugins doctor . --ci
```

Python 3.11+, без runtime-зависимостей (только stdlib). Приоритетная платформа — Linux; Windows поддерживается для разработки.

## Лицензия

Распространяется по Омской Открытой Лицензии — Базовая модификация. Указание авторства. Полный текст — в [LICENSE-RU.md](LICENSE-RU.md), английский перевод — в [LICENSE.md](LICENSE.md).

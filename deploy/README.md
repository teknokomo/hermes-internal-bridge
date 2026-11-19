# Развёртывание Hermes Internal Bridge 0.1.0

Документ для оператора. Всё, что включается вручную — маршруты, replay, установка в профили — перечислено здесь и никогда не делается кодом плагина.

Требования: Hermes Agent **0.23.1+** (`requires_hermes: ">=0.23.1"`), Python 3.11+ (в first-party установке Hermes уже есть). Платформа production — Linux; dev поддерживается на Windows.

## 1. Установить файлы

Вариант A — из Git (рекомендуется, фиксируется commit):

```bash
hermes plugins install teknokomo/hermes-internal-bridge --ref <40-символьный-sha> --no-enable
```

Вариант B — из склонированного каталога (dev):

```bash
git clone https://github.com/teknokomo/hermes-internal-bridge.git
cd hermes-internal-bridge
hermes plugins adopt hermes-internal-bridge   # если каталог уже лежит в ~/.hermes/plugins/
```

Проверка до включения:

```bash
hermes plugins validate ./hermes-internal-bridge --json   # → "ok": true
hermes plugins doctor ./hermes-internal-bridge --ci       # → exit 0, 7 tool(s), 2 hook(s)
```

## 2. Подготовить целевые профили

HIB работает по схеме B: экземпляр плагина в каждом целевом профиле + общая шина на корневом Hermes home.

1. Создать недостающие профили: `hermes profile create <name>`.
2. В **каждом целевом профиле** включить плагин и разрешить injection:

```yaml
# ~/.hermes/profiles/<name>/config.yaml
plugins:
  enabled:
    - hermes-internal-bridge
  entries:
    hermes-internal-bridge:
      allow_gateway_injection: true
```

3. В каталог HIB (`directory.profiles` в настройках плагина) внести все профили-участники — только они считаются валидными целями (`relay_route_missing` для остальных, без отката в default).

## 3. Настроить общую шину

База по умолчанию: `${HERMES_ROOT}/plugin-data/hermes-internal-bridge/hib.sqlite3` (Linux: `~/.hermes/plugin-data/...`; Windows: `%LOCALAPPDATA%\hermes\plugin-data\...`). Директория создаётся приватной: 0700 на Linux, ACL текущего пользователя на Windows. В базе только конверты и метаданные доставки.

Проверка после первого запуска gateway:

```bash
ls -la ~/.hermes/plugin-data/hermes-internal-bridge/   # 0700
hib status
```

## 4. Создать маршруты (оператор)

Маршруты описываются JSON-файлом и добавляются CLI. Включение — отдельный шаг:

```bash
hib routes add --file route.json     # создание/обновление, enabled из файла
hib routes list
hib routes enable route-id           # явное включение оператором
```

Пример `route.json`:

```json
{
  "route_id": "personal-to-work",
  "enabled": false,
  "source": {"platform": "telegram", "user_id": "<USER_ID>", "chat_id": "<DM_CHAT_ID>"},
  "target": {"profile": "work", "chat_id": "<TOPIC_CHAT_ID>", "thread_id": "<THREAD_ID>"},
  "mode": "handoff",
  "priority": 0
}
```

Правила: один маршрут — один destination; два активных маршрута одинаковой специфичности → `route_conflict` (оба не применяются); нет маршрута → `relay_route_missing` (fail-closed). Голосовые: транскрипция выполняется в исходном профиле; настройте `transcription.provider` (например `openai-compat`, ключ — имя переменной окружения в `api_key_env`).

## 5. Активировать

```bash
hermes gateway restart        # multiplex-шлюз обслуживает все профили
hib status                    # маршруты активны, очереди пусты
```

`relay.enabled: false` в конфиге — релей не работает, даже если маршруты включены. Включение релея — решение оператора.

## Операции

- **Replay** (только оператор, с записью в ledger): `hib relay replay <relay_id>`.
- **Dead-letter**: разбор вручную — `hib relay show <relay_id>`, затем replay или удаление через оператора.
- **Ротация секретов**: HIB не хранит секреты; ключи транскрипции живут в env-переменных профиля и ротируются средствами профиля.
- **Немедленная остановка релея**: `hib routes disable <id>` для каждого маршрута или `relay.enabled: false` + `hermes gateway restart`.

## Ручная приёмка 0.1.0 (§14.3, с реальными чатами)

Выполняется не на dev-машине, оператором или агентом основного Hermes владельца.

1. Создать тестовый личный чат и тестовую профильную тему; включить один маршрут `handoff`.
2. Отправить текст в личный чат → в теме ровно одно сообщение, в личный чат ровно один ack («передано»).
3. Отправить голосовое → в теме текст с пометкой транскрипта, оригинал — ссылкой (в конверте), дубликатов нет.
4. Переслать в личный чат сообщение с `@bot` → агент не запускается; в ledger `relay_filtered_forwarded` (`hib status` показывает счётчик).
5. Переключить маршрут в `mirror`, повторить → копия ответа приходит в личный чат, петли нет.
6. Остановить целевой профиль (gateway профиля), отправить сообщение → ошибка `relay_target_unavailable` приходит в личный чат **один раз**, сообщение остаётся `queued`; после старта профиля доставляется.
7. Перезапустить gateway → `queued` сообщения доставляются, потери нет.
8. Handoff с резюме (`hib_bus_send`, kind `handoff.request`) → в целевой теме задача с резюме, полный транскрипт не копируется; в ledger `accepted_by`/`completed_by`.

Критерии: pytest зелёный; `plugins validate --json` → ok; `plugins doctor --ci` → exit 0; сканер секретов по ledger/логам — 0 совпадений; native `profile_routes`, Bot Mode, A2A, Kanban и `hermes-multica-bridge` не изменены.

---
name: hermes-internal-bridge
description: Операции с плагином Hermes Internal Bridge (HIB): статус шины, маршруты, релеи, replay, каталог профилей
---

# Hermes Internal Bridge (HIB)

Плагин доставляет сообщения между профилями через общую шину с политикой маршрутов и аудитом.

## Когда использовать

- Нужно узнать, куда и почему ушло сообщение: `hib status`, `hib relay show <relay_id>`.
- Оператор управляет маршрутами: `hib routes list|enable|disable`.
- Оператор повторяет доставку: `hib replay <relay_id>` (только по `relay_id`, с записью в ledger).
- Нужна карта профилей: `hib directory`, `hib peers` (заготовка 0.3.0).

## Правила

- Включение маршрутов, replay и dead-letter — только оператор.
- Ack означает «принято в шину», а не «профиль ответил».
- В 0.1.0 approval- и federation-конверты отклоняются: `approval_not_supported`, `federation_not_supported`.

# MIGRATION — переход 1.1 → 2.0 (§33 Ph.10)

Правила совместимости — [`Архитектура-2.0.md` §5](../Архитектура-2.0.md).
Кратко: **1.1 продолжает жить** (X96, Orange Pi — на 1.1); 2.0
устанавливается рядом/на отдельный стенд **по отдельному указанию**.

## Что не меняется

* **Данные**: та же `devices.db` (SQLite) — миграция v3 применяется
  автоматически и аддитивно (`device_id`, `ip_history`; backfill);
* **Конфиги**: `settings.json`/`users.json` — те же пути и смыслы,
  новые ключи — аддитивно (§32);
* **API**: старые эндпоинты и ключи остаются, новые поля добавляются;
  удаление — только с миграцией UI;
* **Манифесты**: `module.json` v1 валиден без правок (v2-поля
  `version/capabilities/...` — опциональны, §32);
* **Роли**: профили `roles/*.json` — тот же формат required/optional.

## Что меняется (учесть при переходе)

| Область | 1.1 | 2.0 |
|---|---|---|
| Главная | `/` = устройства | `/` = **dashboard (HOME)**, устройства → `/devices` (§22) |
| Навигация | доменные группы | Application Shell: разделы HOME…ADMIN + вкладки |
| UI-компоненты | `alert()` | `window.toast()` / `window.confirmDialog()` (§23) |
| Долгие операции | синхронно в роуте | `core.jobs` + `/api/jobs` |
| Сетевые правки | ad-hoc | `net.transaction` (prepare/apply/verify/commit/rollback) |
| Hardware | под конкретные платы | **capabilities** (§30, плато-ветвления запрещены) |
| Установка модулей | apt без отката | manifest trust (sha256/publisher) + `syschange` rollback (§26) |
| Демо | статичный слепок | + demo-режим панели: API adapter → fixtures (§28) |

## Порядок перехода

```text
1. Остановить сервис 1.1 (если это тот же хост):
     systemctl disable --now lan-discovery
2. Сделать бэкап: devices.db + /etc/lan-discovery (сделает update.sh)
3. Установить 2.0: sudo ./install.sh    (см. INSTALL.md)
   либо обновить существующую установку: sudo ./update.sh --from <код>
4. Проверить: /api/health (user_version=3), /api/capabilities,
   скан, установку тестового модуля (jobs + rollback)
5. Новый UI: раздел HOME (dashboard), /devices, /storage, /help
```

## Откат на 1.1

* application: `sudo ./update.sh --rollback` (бэкап кода/БД);
* данные: devices.db миграции аддитивны — старая панель работает с
  обновлённой БД? **нет**: v3 добавляет колонки/таблицы, поэтому для
  возврата используйте бэкап devices.db из `/var/backups/lan-discovery`;
* проще всего: 1.1 живёт на своей системе (X96/OP) и не трогается —
  миграция не требуется.

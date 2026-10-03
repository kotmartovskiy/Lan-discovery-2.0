# ARCHITECTURE — LAN Discovery 2.0 (спека §29)

Архитектура 2.0 — **Universal Modular Appliance Platform**. Полное
обоснование и журнал фаз — [`docs/Архитектура-2.0.md`](../Архитектура-2.0.md),
исходная спека — [`docs/Спецификация-2.0.md`](../Спецификация-2.0.md),
аудиты — [`ARCHITECTURE_AUDIT.md`](ARCHITECTURE_AUDIT.md),
[`PORTABILITY_AUDIT.md`](PORTABILITY_AUDIT.md).

## Целевая модель

```text
UI (Application Shell §22)
  ↓  HTTP/JSON (аддитивные контракты)
app.py — assembly root (сборка ctx, регистрация роутов, фоновые треды)
  ↓
modules/  — модули: роуты, парсеры, страницы (не знают про app)
  ↓  только вниз, через контракты
core/     — подсистемы: services/jobs/network/storage/events/roles/
            capabilities/secrets/config/db/… (не знают про modules/app)
  ↓
OS (systemd, apt, sysfs, /proc/device-tree, nmap, netdata, …)
```

Hardware не прописана в коде: **Hardware → Capabilities → Modules →
Roles**. Плата/вендор нигде не упоминаются — только capabilities
(§30, инвариант в `tests/unit/test_portability.py`).

## Структура репозитория

| Путь | Что |
|---|---|
| `app.py` | assembly root: импорты модулей, `register_routes(app, ctx)`, фоновые треды, SocketIO |
| `core/` | подсистемы ядра (24 модуля) — см. [CORE_API.md](CORE_API.md) |
| `modules/<id>/` | модуль: `module.json` + `*.py` + `templates/` + `help.md` — см. [MODULES.md](MODULES.md) |
| `roles/*.json` | декларативные профили — см. [ROLES.md](ROLES.md) |
| `templates/`, `static/` | Application Shell (`base.html`) и страницы |
| `tests/unit/`, `tests/live/` | pytest (маркер `live` — против живого стенда) |
| `tools/` | hw_detect, make_demo, sync_check (инвентарь 1.1) |
| `install.sh` | Installer §25 — см. [PORTING.md](PORTING.md) |

## Границы ответственности (§29)

| Слой | Что принадлежит | Что НЕ принадлежит |
|---|---|---|
| **Core** | долгие операции (jobs), системные сервисы, транзакции сети/хранилища, события, capabilities, БД/конфиг/секреты, идентичность устройства | знание о конкретных модулях/платах, UI |
| **Module** | своя предметная область: роуты, парсеры, страницы, свои API; декларирует capabilities и permissions | доступ к `app` напрямую, чужие таблицы, system-вызовы мимо `core.process/services` |
| **Role** | декларативный набор модулей (`required/optional/conflicts`, always-on) и их включение/выключение | логика модулей, hardware-детект |
| **UI** | отображение + единые компоненты §23 (toast/confirm, loading/error/empty states) | бизнес-логика, системные вызовы |

## Инварианты слоёв (проверяются тестами)

* `modules → app` = **0** (модули получают `ctx` от assembly root, §34/2.0-15);
* `core → app/modules` = **0** (compat-реэкспорт в `app.py` — только для 1.1-совместимости, §32);
* версия — один источник: `core/version.py::APP_VERSION` (§34);
* JSON-контракты API — аддитивно (§32, правило №5);
* новая абстракция core — минимум один потребитель + тест (правило №5);
* application rollback (`update.sh`) ≠ system rollback (`core/syschange`).

## Основные потоки

* **Скан**: `core/discovery.run_scan` → `reconcile` (device identity,
  `core/identity`) → события `device.*` → UI/`/api/dashboard`;
* **Установка модуля**: `POST /modules/<id>/install` → job
  (`core/jobs`) → manifest trust → apt через `core/syschange`
  (backup → apply → verify → rollback) → `set_module_status`;
* **Роли**: `apply_role` → `role_blockers` (compat-check по
  capabilities) → включение/выключение модулей → state;
* **Automation**: событие `events.emit` → `automation.handle_event`
  → правило → действия (`log`/`event`, свои — через `register_action`).

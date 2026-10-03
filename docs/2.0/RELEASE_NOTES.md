# Release Notes — LAN Discovery 2.0.0 (draft, §33 Ph.10)

> Черновик для GitHub Release. Тег `v2.0.0` ставится **по отдельному
> указанию** (см. §34: версия — `core/version.py::APP_VERSION`,
> CHANGELOG сверяется тестом `test_version`).

**LAN Discovery 2.0.0** — Universal Modular Appliance Platform:
панель домашней сети, перепроектированная как модульная платформа
appliance: ядро с контрактами, capabilities вместо знания о платах,
декларативные роли, события и автоматизация, безопасные долгие
операции. Спека — `docs/Спецификация-2.0.md`, архитектура —
`docs/Архитектура-2.0.md`, guides — `docs/2.0/`.

## Что нового

### Ядро и контракты
* **Core subsystems**: services, jobs, network (+транзакции
  prepare/apply/verify/commit/rollback), storage (корни/пути/SMART),
  events (namespace-имена, fan-out), automation (правила
  Event→Action, cooldown), roles (декларативные профили с
  compat-check), capabilities (11 групп), secrets (Fernet, 0600),
  config, db (миграции v1–v3), version (единый источник §34);
* инверсии слоёв устранены: `modules → app` = 0, `core → app` = 0;
  uniform-сигнатура `register_routes(app, ctx)` (§19/§24);
* **device identity**: `device_id` (`mac:`/`ip:`) + `ip_history`,
  миграция БД v3;
* **Jobs**: установка модулей, бэкапы, сканы, системные операции —
  с прогрессом, логами, отменой и событиями `job.*`.

### Безопасность и надёжность
* **Manifest trust**: sha256 index + `trusted_publishers`
  (fail-closed) для каталога модулей;
* **System rollback §26**: транзакции apt/файлов
  `preflight → backup → apply → verify → rollback` (application
  rollback — в `update.sh`);
* **Config & Secrets §21**: секреты в `kv.json` (Fernet at rest,
  0600) отдельно от settings.

### Hardware и портируемость
* **Capabilities — главный механизм hardware compatibility** (§35):
  модули декларируют требования, роли проверяют совместимость;
* **Portability §30**: плато-специфика убрана из кода (grep-тест в
  CI): Orange Pi / X96 Max / x86_64 — через capabilities;
* **Installer §25**: `install.sh` — preflight → hw-detect → deps
  (критичные обязательны, опциональные best-effort) → venv →
  modules → config → db → systemd → health; `--dry-run`;
* **Appliance smoke test §27**: 12-шаговая цепочка «чистая
  установка → … → rollback» в unit + live.

### UI 2.0
* **Application Shell §22**: разделы HOME…ADMIN, dashboard на HOME
  (6 ответов: всё ли нормально / что происходит / устройства /
  jobs / проблемы / предупреждения), `/devices`, `/storage`;
* единые компоненты §23: toast, confirmDialog, loading/error/empty
  states, responsive, accessible (aria-current, live-regions);
* **Demo mode §28**: API adapter (UI → Real|Demo API → fixtures),
  production-HTML без ручного копирования; статичное демо-сайтное
  генерирование сохранено (`tools/make_demo.py`).

### Документация
* 11 guides §29 в `docs/2.0/` (ARCHITECTURE, CORE_API, MODULES,
  CAPABILITIES, ROLES, NETWORK, STORAGE, JOBS, SECURITY,
  DEVELOPMENT, PORTING);
* INSTALL / UPGRADE / MIGRATION (§33 Ph.10), аудиты
  ARCHITECTURE/PORTABILITY.

## Системные требования
* Debian/Ubuntu/Armbian, Python ≥ 3.9 (CI: 3.11), systemd, apt;
* ARM (Allwinner/Amlogic) и x86_64 — без правок кода;
* данные: `devices.db` (SQLite, `user_version=3`),
  конфиги `/etc/lan-discovery/`.

## Установка и обновление
* чистая установка: `docs/2.0/INSTALL.md` (`./install.sh`);
* обновление/откат: `docs/2.0/UPGRADE.md` (`./update.sh`);
* переход с 1.1: `docs/2.0/MIGRATION.md`.

## Известные ограничения
* 2.0 **не деплоится** на X96/Orange Pi (работают на 1.1) — живой
  прогон на платах выполняется при выделенном стенде;
* Werkzeug dev-сервер под root без TLS — принято «root by design»
  до 01.01.2027 (`docs/Безопасность.md`);
* демо-фикстуры генерируются с хоста панели
  (`make_demo.py --fixtures`), отдельного публичного демо-стенда нет.

## Проверка
CI (GitHub Actions): unit-тесты (400+), `py_compile`, `bash -n` +
`install.sh --dry-run`. Подробности — `docs/2.0/DEVELOPMENT.md`.

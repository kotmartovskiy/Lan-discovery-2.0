# ARCHITECTURE_AUDIT — LAN Discovery 2.0 (спека §37, Phase 0)

**Дата:** 02.10.2026 · **Коммит-база:** `21bcf7b` · **Статус:** DONE (PHASE 2.0-9)

Аудит выполнен read-only обходом репозитория (структура, `core/`, module
loader/catalog, capabilities, roles, discovery, network, storage,
installer/update/recovery, tests) + AST-граф импортов. Массового
редактирования кода для аудита не делалось.

---

## A. Current architecture

### Состав (база `21bcf7b`)

```text
app.py (474)            — assembly root: импорты 14 модулей, ручные
                          register_routes(...), фоновые треды (scan,
                          currencies, recycling, schedule, events retention),
                          SocketIO, конфиг хост/порта
core/ (16 плоских модулей)
  capabilities.py (540) — collect(): 11 групп (network/storage/hardware/
                          radio/camera/media/service/board/thermal/tools/
                          checked_at), состояния present/absent/unknown ×
                          measured/detected/unverified, TTL-кэши
  config.py (64)        — load/get/save/clear_cache (settings.json)
  dashboard.py (10)     — merge_alerts (склейка предупреждений+событий)
  discovery.py (409)    — scanner(nmap) → reconcile → events; тред скана
  events.py (~150)      — add_event/list/cleanup/retention_loop; severity
  hardware.py           — board/thermal/USB-идентификация платы
  jobs.py (585)         — JobManager: submit/get/list/cancel/wait,
                          sqlite jobs, recover_interrupted, retention
  manifest.py           — манифест v2 schema + PERMISSIONS_GRID
  module_catalog.py(298)— fetch_index/sha256/trust/install_module/remove
  module_loader.py(486) — discover/status/compute_status/nav_items/app_items
  network.py (776)      — list_interfaces/addresses/routes, wifi_scan,
                          class Transaction (prepare→apply→verify→rollback,
                          insurance), set/del address/route
  process.py (46)       — run/out (timeout, shell-auto, utf-8)
  roles.py (423)        — validate/load/apply_role/blockers/overview
  samba_guest.py (218)  — transform/guest_state/apply (testparm+smbcontrol)
  services.py (96)      — status/control(start..disable,reload)/logs/health
  storage.py            — ROOTS/path/DB_BACKUP_DIR/EMMC_BACKUP_PATH/
                          clone_disk/lsblk_text/df_text/smart_report
modules/                — 12 route-файлов + 33 каталога модулей (module.json)
  system_routes.py(2329), media_routes.py(1647), core_routes.py(1063),
  devices_routes.py(629) — ВЛАДЕЕТ схемой devices/events (init_db_schema)
  всего 189 @app.route (media 64, core 30, system 29, network 24…)
templates/              — два shell: base.html (1777 строк, страницы) +
                          base_app.html (163, apps/*); навигация —
                          module_loader.nav_items/nav_groups
Инфраструктура          — install.sh (идемпотентный), update.sh (бэкап →
                          apply → verify → авто-откат уровня кода/БД),
                          recovery.sh, restore_server.py (автономный),
                          deploy.py, backup-db (systemd → /srv/backup-db)
Тесты                   — 324 unit (28 файлов), live smoke+security
                          (15, деселект локально), CI ubuntu/py3.11
Демо                    — tools/make_demo.py: рендер через test_client +
                          фикстуры API + demo.js перехват fetch (адаптер)
```

### Dependency graph (агрегат AST-импортов)

```text
app.py     -> modules : 14    app.py     -> core : 4
modules    -> core    : 21 ✓  modules    -> app  : 9  ✗
core       -> core    : 13 ✓  core       -> app  : 5  ✗
modules    -> modules : 14    core       -> modules: 3 ✗
```

Точечные инверсии (прямые цитаты):

- `core/discovery.py:32 from app import _cfg`; `:368 from modules.devices_routes import get_db`
- `core/events.py:136 from app import _cfg`; `:140 from modules.devices_routes import get_db`
- `core/jobs.py:279 from modules.devices_routes import DB`; `:331 from app import _cfg`
- `core/module_loader.py:213 / module_catalog.py:73 from app import APP_VERSION`
- `modules/* → app` (9): auth, inventory, monitor, weather… (утилиты app)

### Владение данными (sqlite `devices.db`)

- `devices`: **`ip TEXT PRIMARY KEY`** (modules/devices_routes.py:32) —
  идентичность = IP.
- `events`: id/timestamp/ip/hostname/mac/event + severity/source/metadata
  (через ALTER-миграции), ретеншн в core/events.
- `jobs`: core/jobs (своя таблица), `inventory`: modules/inventory.
- Конфиг/пользователи/секрет: `/etc/lan-discovery/` (settings.json,
  users.json, secret.key), код `/opt/lan-discovery`, данные `/srv/*`.

---

## B. Target architecture (спека §14–37, кратко)

- **Ядро-фасады с явным API** (§19): `network/storage/services/jobs/events/
  capabilities/modules/roles`, меньше `core → app/modules` (только вниз).
- **Единая DB-слоистость**: core владеет соединением/схемой; модули получают
  коннектор через контекст.
- **device_id → MAC → hostname → IP history** (§15), события именованы
  `device.* / network.* / storage.* / job.* / module.* / system.*` (§16) и
  служат фундаментом Automation (§17: Event → Rule → Action).
- **Module contract v2** (§19/§24): uniform register-контекст, manifest v2
  (version/capabilities/permissions/sha256), модули без Linux-деталей и
  захардкоженных путей.
- **Config-модель** (§20): core/module/role/secrets/runtime state; **secrets
  отдельно** (§21).
- **UI shell** (§22–24): Application Shell → Navigation → Module Page; UI не
  знает Linux.
- **Installer/rollback/smoke** (§25–27): preflight, application vs system
  rollback, appliance smoke test; **demo через API-адаптер** (§28).
- Портативно (§30), offline/local-first (§31), compat через адаптеры (§32),
  один version source (§34).

---

## C. Migration map (CURRENT → TARGET)

| Subsystem | CURRENT | TARGET (спека) |
|---|---|---|
| Идентичность | `devices.ip` PK, события NEW/ONLINE/OFFLINE/MAC_CHANGED/IP_CHANGED | §15 device_id + ip_history, composite fingerprint; события §16 |
| Events | только discovery-emitter, таблица+severity, фид UI | именованные события всех подсистем + `events.emit/subscribe` (§16) |
| Automation | нет | компактный rules engine (§17), не HA-клон |
| Core→app | 5 импортов `_cfg/APP_VERSION/get_db` | core/config + core/version + core DB; инверсии = 0 |
| Модули | register_routes с 4 разными сигнатурами, `→ app` ×9 | uniform module context; Linux — только через core (§24) |
| DB | схема в modules/devices_routes | core/db владеет; devices_routes — re-export (§32 compat adapter) |
| Манифесты | v2 schema готова; 33 builtin-манифеста legacy (0 с `version`, deps=apt/ports) | builtin → v2: version+capabilities+permissions |
| Config/Secrets | settings.json + users.json + secret.key в /etc | §20 классы конфигов, §21 secrets API |
| Rollback | update.sh = app-level (код+БД+конфиг) | + system-level: preflight/backup/apply/verify/rollback (§26) |
| Installer | install.sh (idempotent, Debian/Armbian) | §25 preflight + hardware detection + health check, без платы-специфики |
| UI | два shell (base/base_app), 189 роутов | §22 Application Shell + разделы HOME…ADMIN; §24 UI→API→Core→Linux |
| Demo | make_demo: test_client-рендер + фикстуры + demo.js fetch | §28 тот же принцип, оформленный как API adapter |
| Smoke | live smoke/security (заголовки/контролы) | §27 appliance-цепочка clean install → … → rollback |

---

## D. Components that should NOT be rewritten

1. **Flask-приложение в целом** (§31: без rewrite Python/Flask) — собираем,
   не переписываем.
2. **core/jobs.py** — уже соответствует §33 Phase 2 (submit/cancel/wait,
   persist, recover, retention); расширять, не менять.
3. **core/capabilities.py** — модель состояний/надёжности и TTL-кэши
   (инвариант 2.0-1); таксономия расширяется, механизм остаётся.
4. **core/network.py Transaction** (2.0-6) — prepare→apply→verify→rollback +
   insurance; основа для §33 Phase 3.
5. **core/storage.py path/clone** (2.0-7) + backup-константы.
6. **core/roles.py + roles/*.json** (2.0-5) — поля уже = модели §14; UI/API
   apply не ломаем.
7. **core/manifest.py + module_catalog trust** (2.0-4) — sha256/trusted/
   compat; без PKI (§31).
8. **install.sh / update.sh / recovery.sh / restore_server.py** — рабочая
   механика 1.1; расширять §25–26, не выбрасывать.
9. **auth** (users.json + хэши + secret.key + rate-limit + CSRF-проверки) —
   covered live-тестами.
10. **Обнаружение платы** (core/hardware._board, capabilities) — уже
    централизовано; §30 плата-специфики нет (кроме install.sh-проверок).
11. **Шаблоны страниц по содержимому** — перекладываем в shell позже (§22),
    не редактируем в процессе core-фаз.
12. **restore_server.py / weather-monitor** — автономны, вне core.

---

## E. Components requiring refactoring

1. **`core → app/modules` инверсии** (5+3): `_cfg` → core.config;
   `APP_VERSION` → core/version (единый источник, §34); `get_db/DB` →
   core/db. Фаза 2.0-10.
2. **Владение схемой БД** — init_db_schema переезжает в core/db,
   devices_routes получает compat-импорт (§32).
3. **Сигнатуры register_routes** — 4 варианта (app / +login/admin /
   +can_edit/_cmd/_cfg/page_data / +service_state): ввести единый
   module context (§G).
4. **`modules → app` ×9** — оставшиеся утилиты app (_cfg, page_data) →
   core/config + core/ui-context.
5. **device identity** — devices.ip PK → device_id (+ip_history, §15) с
   миграцией БД и адаптерами (§32).
6. **Events** — именованные события + фасад emit/subscribe; только
   discovery-emitter (§F, §16).
7. **Монолиты**: system_routes 2329 / media_routes 1647 / core_routes 1063 —
   разбивать при добавлении функционала (не mass-split до потребности).
8. **Builtin-манифесты** — 0/33 имеют `version`, capabilities-требования
   не объявлены (deps только apt/ports, 19/33) → миграция на v2.
9. **UI-двойной shell** (base.html 1777 строк vs base_app.html) → §22.
10. **Захардкоженные пути**: system_routes ×32, capabilities ×9, media ×5
    (частично — legacy backup targets, сознательно оставлены в 2.0-7).

---

## F. New Core APIs (явные, §19)

Уже есть (покрыть документацией, не переписывать):

```text
network.list_interfaces/list_addresses/list_routes
network.Transaction + set/del address/route (+ insurance)
storage.path/clone_disk/lsblk_text/df_text/smart_report/backup-цели
services.status/control/start/stop/restart/enable/disable/reload/logs/health
jobs.submit/get/list/cancel/wait (+ retention)
capabilities.collect/probe_tools
modules.install_module/remove_module/fetch_index (sha256/trust)
roles.apply_role/role_blockers/roles_overview
process.run/out
config.load/get/save
```

Новые (по приоритету):

```text
core/version.py       APP_VERSION — единственный источник (§34)
core/db.py            connect/init_schema/migrations — владение схемой
core/identity (§15)   resolve/fingerprint/ip_history/device_id
events.emit(name, subject, payload)      (§16) — фасад поверх таблицы
events.subscribe(handler)                — для automation (§17)
secrets.get/set/delete                   (§21)
storage.devices()/mount()/unmount()      — JSON-поверх lsblk_text (§19)
```

## G. New module contracts

1. `register_routes(app, ctx)` — единая сигнатура; `ctx` даёт
   `login_required/admin_required/can_edit`, `config`, `events`, `jobs`,
   `process`, `services`, `paths` (storage.path). Миграция по одному
   модулю (§33 Phase 1: define API → adapter → migrate one → test).
2. В модулях **запрещено**: `subprocess`, `systemctl`, захардкоженные
   `/etc|/opt|/srv` — только core.process/core.services/storage.path
   (уже достигнуто для строк инвентаризации 47–56; остатки — media/system).
3. Манифест v2 обязателен для новых/каталожных модулей; legacy-базовые —
   постепенно: `version`, `capabilities`, `permissions`, `conflicts`.
4. Длинные операции — через `jobs.submit` (§33 Phase 2; уже: install,
   backup, scan — дополнительно: clone, iptv-update, эвакуация и пр.).
5. События подсистем — только через `events.emit` с именем namespace.

## H. New role model

Уже реализовано (2.0-5): `roles/*.json` с `required_modules,
optional_modules, capabilities, hardware_requirements, dependencies,
conflicts, recommended_configuration, security_profile`; validate/load/
blockers/apply/overview; compat через capabilities+compute_status.

Что дополняет спека §14 и ещё не сделано:

- `security_profile` — бейдж без enforcement (осознанно 2.0-5); привязка к
  permissions-сетке — при появлении per-route enforcement.
- Роли-примеры спеки покрыты 6/7 (**SDR Gateway** — по мере модулей
  sdr/spectrum/recording; capabilities `radio.sdr` уже есть).
- Новые роли = JSON без изменения Core — подтверждено архитектурой
  loader'а (инвариант §36 «новые роли без изменения Core»).

## I. Security boundaries

Границы сейчас:

- **Вход**: `/etc/lan-discovery` — users.json (хэши), secret.key (32 байта),
  settings.json; rate-limit на login; CSRF (live-тест); security-заголовки
  и safe cookie (live-тесты `test_live_security.py`, 8 тестов).
- **Права**: `PERMISSIONS_GRID` (network/storage/services/process/…) —
  применяется при установке модуля (opt-in), per-route enforcement нет.
- **Каталог**: sha256 тарболла, trusted sources/publisher — opt-in
  (`require_sha256`), без PKI (§12/§31).
- **Сеть**: SSRF-проверки IPTV (`test_iptv_ssrf.py`), live-контроль API.
- **Привилегии**: панель под root (systemctl/nft), поэтому контракт
  «модуль не исполняет произвольное без прав» критичен.
- **Пробелы к §21**: секреты (пароли/токены/ключи VPN) хранятся в
  settings/users без отдельного secrets-хранилища; passwords-модуль —
  выделить в фазу Config/Secrets.
- **restore_server.py** — вне auth-контура панели (автономный), лежит на
  диске с кодом; документирован в «Восстановление».

## J. Jobs architecture

Текущее (соответствует §33 Phase 2): sqlite-таблица, worker-треды,
queued/running/completed/failed/cancelled, progress/logs/cancelable,
recover_interrupted (после падения), retention, REST `/api/jobs*`,
UI-виджет. Потребители: module install/update, db-backup, network scan.

Доработки: clone/emmc, iptv-update, system ops (все «долгие» из §33);
`job.started/completed/failed` события (§16) — сцепка J↔K.

## K. Network architecture

Текущее: read-only интерфейсы/адреса/маршруты, wifi_scan, валидация,
Transaction c rollback/insurance, роуты настройки с UI-warning; nettools и
wifi через core.process (2.0-6, внефазная доводка).

Цель (§33 Phase 3): router/dhcp/dns/firewall/vpn — **независимые модули**
над core; firewall — после проектирования модели nft (записано в 2.0-6);
без «if x96max» — только capabilities (§30).

## L. Storage architecture

Текущее: ROOTS+path (posixpath), backup targets (legacy /srv/backup-db и
/srv/backup-system — сознательно сохранены, журнал 2.0-7), clone_disk
(dd+прогресс+отмена), lsblk/df/smart абстракции.

Цель (§19/§33 Phase 4): `storage.devices()/mount()/unmount()` JSON,
`storage.inserted/removed/health_changed` события (§16) → automation
«health_warning → backup» (§17); shares — по мере UI-потребителя.

## M. Testing strategy

- **Unit**: 324 (28 файлов; CI = `pytest tests/unit`, ubuntu/py3.11; локально
  2 известных Windows-фейла + 3 linux-skip — документировано).
- **Live**: smoke (health/login/pages) + security (8) — против 1.1-панели;
  для 2.0 потребуется живая цель (см. риски N).
- **Пробелы**: нет appliance smoke-цепочки §27; нет тестов
  inventory/monitoring-роутов (частично), интеграции install.sh/update.sh;
  automation — после фазы; demo-lint есть (`tools/demo_lint.py`).
- **Приоритет §27**: appliance smoke важнее мелких UI-тестов — отражено в
  плане §O (фаза Smoke).

## N. Migration risks

1. **Смена PK devices (ip → device_id)** — самая рискованная: нужна
   миграция sqlite + compat-виды/алиасы (§32), обратный порядок (сначала
   identity-слой поверх старой таблицы, потом switch).
2. **Переименование событий** (NEW/ONLINE → device.online) — UI/фильтры/тесты
   читают старые строки: dual-read адаптер, потом dual-write, потом switch.
3. **core→app разрыв** — порядок импортов: сначала core/version+config+db,
   потом удаление старых ссылок (иначе цикл при import app).
4. **Uniform context** — 12 register_routes × N вызовов в app.py; миграция
   по одному модулю с зелёными тестами.
5. **Внешние репо**: catalog index (Lan-discovery-modules), docs-public,
   demo — синхронизировать после контрактных изменений.
6. **Нет живой цели для 2.0** (AGENTS: X96/OP на 1.1) — portability/smoke
   фазы требуют стенда (решение/санкция пользователя).
7. **System rollback** (§26) затрагивает apt/kernel — только на тестовом
   стенде, вручную.

## O. Phased implementation plan (§33 → текущий статус)

Уже закрыто (журнал ROADMAP): Phase 0 (этот аудит — 2.0-9), Phase 1 (частично:
core/process, services, config, capabilities, jobs, manifest — 2.0-1…2.0-4),
Phase 2 (Jobs — 2.0-3), Phase 3 (Network Core — 2.0-6; dhcp/dns/firewall/vpn
ждут модулей), Phase 4 (Storage Core — 2.0-7), Phase 5 (Module security —
2.0-4), Phase 6 (Roles 2.0 — 2.0-5, модель §14).

Предлагаемый порядок фаз (таблица ROADMAP будет дополнена записью в §3):

| Фаза | Спека | Состав |
|---|---|---|
| 2.0-10 Core independence | §19, §34, Phase 1-долг | core/version (APP_VERSION), core/db (схема+миграции), удалить core→app/modules (8 точек), compat-reexport |
| 2.0-11 Device identity | §15 | device_id, ip_history, fingerprint; миграция devices PK через адаптеры |
| 2.0-12 Events 2.0 | §16 | именованные события (device/network/storage/job/module/system), emit-фасад, dual-read совместимость, эмиттеры из jobs/module-manager |
| 2.0-13 Automation | §17 | правила event→rule→action, хранение, минимальный UI/API; компактный engine |
| 2.0-14 Config & Secrets | §20, §21 | классы конфигов, secrets.get/set/delete, вынос паролей токенов из settings |
| 2.0-15 Module contract v2 | §19, §24, G | uniform module context, миграция модулей по одному, builtin-манифесты → v2 (version/capabilities) |
| 2.0-16 System rollback | §26 | preflight/backup/apply/verify/rollback для apt/системных пакетов в update-процессе |
| 2.0-17 Appliance smoke | §27 | интеграционная цепочка clean install → … → rollback (на стенде) |
| 2.0-18 Installer 2.0 | §25 | preflight + hardware detection + health check, ARM/x86, без платы-специфики |
| 2.0-19 UI 2.0 shell | §22–24 | Application Shell, разделы HOME…ADMIN, dashboard-ответы, единые диалоги/уведомления |
| 2.0-20 Portability | §30 | Orange Pi, X96 Max, x86_64 — через capabilities, без if-board |
| 2.0-21 Release | §33 Ph.10, §34 | v2.0.0, guides, документация §29 (CORE_API/MODULES/…), demo, release notes |

Правила фаз не меняются: одна фаза — один контракт, тесты зелёные,
аддитивность API (§32), journal в ROADMAP, CI на каждый push.

---

**Вывод (§37):** аудит завершён до mass-editing; code-изменения начинаются с
2.0-10 (Core independence) — она разрывает инверсии зависимостей и даёт
остальным фазам опору (db/version/config).

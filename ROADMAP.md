# ROADMAP.md — LAN Discovery 2.0 (Universal Modular Appliance Platform)

**Старт:** 01.10.2026 (PHASE 2.0-0)
**Входное ТЗ:** `docs/Спецификация-2.0.md`
**Архитектура и обоснования:** `docs/Архитектура-2.0.md`
**История версии 1.1:** `docs/ROADMAP-1.1.md` (весь журнал PHASE 0–16)

Правила (спека §0): не начинать с переписывания; изучить код → границы →
готовые механизмы → отделить долг от необходимого → проектировать →
реализовывать; каждое крупное изменение — с обоснованием
(в `docs/Архитектура-2.0.md` или в задаче фазы).

---

## 1. Фазы 2.0

| Фаза | Название | Состав | Статус |
|---|---|---|---|
| **2.0-0** | Foundation | репозиторий 2.0 (клон 1.1, remote `upstream-11`), архив спецификации, архитектурный аудит 1.1, этот ROADMAP, `APP_VERSION=2.0.0`, AGENTS под новый репо | **DONE (01.10.2026)** |
| **2.0-1** | Capabilities 2.0 | таксономия `network.* / storage.* / hardware.* / radio.* / camera.* / media.* / service.*` поверх `core/capabilities.py` (модель present/absent/unknown × measured/detected/unverified сохраняется); расширение `collect()`; `/api/capabilities` аддитивно; UI `/capabilities` с группировкой; тесты compat-чеков модулей от новых capability | **DONE (01.10.2026)** |
| **2.0-2** | Core skeleton + инвентаризация | `core/services.py` (status/start/stop/restart/enable/disable/logs/health), `core/process.py` (безопасный запуск команд), `core/config.py` (settings без зависимости от app); таблица-инвентаризация всех вызовов `subprocess`/`systemctl` (18/13 файлов) с планом переноса; контракты `core/network.py`/`core/storage.py` read-only; первый перенос (1–2 вызова) с тестом | **DONE (01.10.2026)** |
| **2.0-3** | Jobs subsystem | `core/jobs.py`: manager + worker-треды, состояния queued/running/completed/failed/cancelled, поля id/type/status/progress/started_at/finished_at/logs/result/error/cancelable; sqlite-таблица `jobs` (retention как у events); `GET /api/jobs` + `POST /api/jobs/<id>/cancel`; UI-виджет активных jobs; миграция на jobs: module install/update, backup/restore БД, network scan | **DONE (02.10.2026)** |
| **2.0-4** | Module manifest 2.0 + permissions + trust | схема v2 (capabilities/dependencies/conflicts/services/configuration/role_support + publisher/sha256/min_core_version/max_core_version) — back-compat v1; сетка прав (network/storage/services/process/camera/usb/gpio/serial) в манифесте и UI-запрос при установке; catalog: SHA-256 тарболла из index, trusted sources, core-compat check; без PKI/sandbox (спека §11–12) | **DONE (02.10.2026)** |
| **2.0-5** | Roles 2.0 | `roles/<id>.json` манифесты (required_modules/optional_modules/capabilities/hardware_requirements/dependencies/conflicts/recommended_configuration/security_profile); загрузчик в `core/roles.py`, compat через `compute_status()`/capabilities; роли-примеры: Network Gateway, Home Server, Remote Site, Industrial Gateway, Network Diagnostic Box, Camera Gateway (SDR — ждём хвост спеки); apply/API не ломаем (ALWAYS_ON сохраняется) | **DONE (02.10.2026)** |
| **2.0-6** | Network Core (транзакционный) | `core/network.py`: объекты interfaces/addresses/routes/firewall/…; каркас Prepare→Apply→Verify→Commit/Rollback; UI-warning «This operation may disconnect the current session»; авто-rollback; первый перенос: sys-network-операции; DHCP/DNS/VPN/AP/bridge — по мере модулей-потребителей | **DONE (02.10.2026)** |
| **2.0-7** | Storage Core | `core/storage.py`: корни `/srv/media|data|backup`, disks/partitions/mounts/SMART (lsblk/smartctl уже в TOOL_PROBES), shares/backup targets; `storage.path()` для модулей; миграция констант модулей (IPTV_DIR/MEDIA_DIR/PLAYLISTS_DIR и др.) | **DONE (02.10.2026)** |
| **2.0-8** | Хвост спецификации | §15–37 получен 02.10.2026 → `docs/Спецификация-2.0.md` дополнен (§0–37 целиком) | **DONE (02.10.2026)** |
| **2.0-9** | Architecture Audit (спека §37 Phase 0) | обход репо (core/loader/catalog/capabilities/roles/discovery/network/storage/installer/update/recovery/tests), dependency graph, `docs/2.0/ARCHITECTURE_AUDIT.md` (A–O) → план фаз по §33 | **DONE (02.10.2026)** |
| **2.0-10** | Core independence (§19, §34) | `core/version` (APP_VERSION — единый источник), `core/db` (схема+миграции, владение данными), удалить инверсии `core→app/modules` (8 точек: `_cfg`, `APP_VERSION`, `get_db/DB`), compat-reexport из devices_routes | **DONE (02.10.2026)** |
| **2.0-11** | Device identity (§15) | `core/identity.py` (derive_id `mac:`/`ip:`, record_ip, identity_of), миграция v3 (device_id + ip_history + backfill, без смены devices.ip PK), reconcile присваивает/наследует device_id, аддитивный `GET /api/device/<ip>/identity`; события не тронуты | **DONE (02.10.2026)** |
| **2.0-12** | Events 2.0 (§16) | namespace-имена `device./network./storage./camera./job./module./system.` (`NAMESPACE_EVENTS`, строгие для emit), фасад `events.emit/subscribe` (fan-out подписчикам, ошибки не роняют писателя), dual-read `list_events` (legacy⇄namespace), потребители: jobs `job.started/completed/failed/cancelled`, `wait()` = терминальный+финализирован (read-after-wait) | **DONE (02.10.2026)** |
| **2.0-13** | Automation (§17) | `core/automation.py`: правила Event→Rule→Action (`automation_rules`, ensure-таблица), матчинг dual-read + cooldown + guard от петель, реестр действий (`log`/`event`, расширение модулями), engine на `events.subscribe` + `automation.start()` в app, CRUD API `/api/automation/rules*`, минимальный UI `/automation` (nav Система, admin), тесты | **DONE (02.10.2026)** |
| **2.0-14** | Config & Secrets (§20–21) | классы конфигов в docstring `core/config` + единый справочник путей (`SETTINGS_PATH`/`MODULES_STATE_PATH`/`ROLES_STATE_PATH`, потребители module_loader/roles/module_catalog); `core/secrets.py`: `get/set/delete/keys` (kv.json, Fernet at rest, 0600) + crypto перенесён из core_routes (compat-алиасы), токен каталога → secrets с fallback на settings; тесты | **DONE (02.10.2026)** |
| **2.0-15** | Module contract v2 (§19, §24) | uniform module context (`register_routes(app, ctx)`), миграция модулей по одному, builtin-манифесты → v2 (version/capabilities) | **DONE (02.10.2026)** |
| **2.0-16** | System rollback (§26) | preflight→backup→apply→verify→rollback для apt/системных изменений (app-level rollback уже в update.sh) | **DONE (02.10.2026)** |
| **2.0-17** | Appliance smoke test (§27) | интеграционная цепочка install→…→failure→rollback (на стенде; важнее мелких UI-тестов) | **DONE (03.10.2026)** |
| **2.0-18** | Installer 2.0 (§25) | preflight + hardware detection + health check, ARM/x86_64/минимальные установки, без платы-специфики | **DONE (03.10.2026)** |
| **2.0-19** | UI 2.0 shell (§22–24) | Application Shell → Navigation → Module Page, разделы HOME…ADMIN, dashboard-ответы, единые диалоги/уведомления/иконки | **DONE (03.10.2026)** |
| **2.0-20** | Portability (§30) | Orange Pi, X96 Max, x86_64 — через capabilities, hardware-specific в Hardware Detection | **DONE (03.10.2026)** |
| **2.0-21** | Release (§33 Ph.10, §34) | v2.0.0 + guides + документация §29 (CORE_API/MODULES/CAPABILITIES/…) + demo + release notes | **DONE (03.10.2026)** |

Статус фазы — только `pending` / `**DONE (дд.мм.гггг)**`; изменения
состава — с записью ниже (§3).

---

## 2. Инварианты (не нарушать в любой фазе)

1. Тесты зелёные на каждой фазе (unit на CI + live на X96 **для
   переносимого**; сам 2.0 на X96 не деплоится — живые проверки только
   когда появится цель).
2. `APP_VERSION` = `2.0.0` до первой функциональной задачи, тег `v2.0.0`
   — на релиз.
3. Cherry-pick фиксов безопасности: 1.1 (боевой) → 2.0.
4. Аддитивность API/манифестов (см. `docs/Архитектура-2.0.md` §5).
5. Новая абстракция = минимум один потребитель + обоснование.

---

## 3. Журнал выполнения

### 01.10.2026 — PHASE 2.0-0: Foundation — **DONE**

- Создан приватный репозиторий `kotmartovskiy/Lan-discovery-2.0`;
  локальный клон `C:\Users\Lenovo\Documents\Lan-discovery-2.0`
  (полная история 1.1 вплоть до `367b307`), remote:
  `origin` → 2.0, `upstream-11` → `Lan-discovery-1.1` (cherry-pick).
- `docs/Спецификация-2.0.md` — архив входного ТЗ (§0–§14, обрыв на
  «## SDR» зафиксирован, хвост ждём).
- `docs/Архитектура-2.0.md` — аудит 1.1 по слоям (таблица
  «что уже соответствует»), gaps с обоснованиями и привязкой к фазам,
  долги «не трогать», правила совместимости 1.1↔2.0, черновики
  контрактов core API.
- `ROADMAP.md` переписан под 2.0 (старый → `docs/ROADMAP-1.1.md`).
- `AGENTS.md` — секция репозиториев/окружения переписана под параллельную
  работу 1.1 и 2.0.
- `APP_VERSION` → `2.0.0`, `CHANGELOG.md` → раздел `## [2.0.0]`,
  README — «2.0 (в разработке)».

### 01.10.2026 — PHASE 2.0-1: Capabilities 2.0 — **DONE**

- `core/capabilities.py` — таксономия 2.0 (все пробы read-only,
  sysfs/proc/dev/PATH): группы `network` (ethernet/wifi/wifi_ap/
  multiple_interfaces), `hardware` (usb/gpio/uart/rs485/i2c/spi/onewire),
  `radio` (sdr/subghz), `camera` (usb/ip), `media` (audio/video),
  `service` (systemd/docker); в `storage` добавлены `local`/`removable`/
  `smart`. Старые ключи 1.1 — без изменений (аддитивность §5).
- Правила честности (§0): корень источника недоступен → unknown/unverified;
  поиск выполнен и пуст → absent/detected; содержимое прочитано →
  value + measured. SDR — только подтверждённые USB ID (rtl-sdr/hackrf/
  airspy); subghz при живом SPI → unknown (CC1101 не исключить);
  docker CLI без сокета → unknown; `camera.ip` — только по конфигу панели
  (сеть не сканируем); `wifi_ap` требует Wi-Fi-интерфейса + hostapd/конфиг.
- `TOOL_PROBES` += `hostapd`, `docker` (аддитивно для `/api/health`).
- UI `/capabilities`: макрос `cap_row`, секции шести групп; `/api/capabilities`
  — роуты без изменений (аддитивный JSON).
- Тесты `tests/unit/test_capabilities.py`: таксономия + инварианты
  достоверности, ~20 новых кейсов (wifi_ap-логика, sdr/subghz/docker/
  camera.ip/audio/storage.local, `_glob`); `compute_status()` не менялся —
  manifest `hardware.storage` может требовать `storage.local` бесплатно.
- Локально (Windows): `collect()` без падений, инварианты чистые,
  рендер шаблона ок; CI unit — прогнан после push этой фазы.

### 01.10.2026 — PHASE 2.0-2: Core skeleton + инвентаризация — **DONE**

- `core/process.py` — единственная точка запуска команд: `run()`
  (список аргументов, timeout, utf-8, CompletedProcess) и `out()`
  («stdout.strip() или ""», стиль `_cmd`).
- `core/services.py` — facade systemd (спека §8): `status/control/
  start/stop/restart/enable/disable/health/logs`; ответы — словари без
  исключений (кроме `ValueError` на неверный action), таймаут →
  `{"ok": False, "timeout": True}`.
- `core/config.py` — единый источник `settings.json` (путь, кэш 10 с,
  атомарный `save`, `get` в стиле `_cfg`) без зависимости от app.
- Контракты read-only: `core/network.py:physical_ifaces()` (спека §5,
  транзакционность — в 2.0-6), `core/storage.py:lsblk_text/df_text/
  smart_report()` (спека §6, полный Storage Core — в 2.0-7).
- **Инвентаризация** `docs/Инвентаризация-core-2.0.md`: 71 прямой вызов
  в 13 файлах (11 runtime + remote_edit/restore_server), systemctl — в
  9 py-файлах; план переноса по файлам и фазам (2.0-3 — обёртки и
  status/journalctl; 2.0-6 — nettools/wifi; 2.0-7 — clone; вне core —
  recovery/dev/Popen-проигрыватели).
- **Первый перенос (5 точек, с тестами)** — все JSON-контракты роутов
  сохранены: `_check_service` → `core.services.status`;
  `/api/service/<svc>/<action>` → `core.services.control` (400/500/504
  как раньше); `/api/disks` → `core.storage.*`;
  `capabilities._net_ifaces` → `core.network.physical_ifaces` (убрано
  дублирование sysfs из 1.1); `system_routes.load_settings` →
  `core.config.load`.
- Тесты: `tests/unit/test_core_layer.py` — 20 кейсов (процесс:
  реальный запуск/таймаут/shell; services: status/control/timeout/health/
  роут-контракты; config: roundtrip/corrupt/delegation; network: fake
  sysfs-дерево; storage: контракт + `/api/disks`).
- Локальный смоук (Windows): все модули без исключений (`unknown`/
  `absent` при отсутствии systemctl/sysfs), инварианты `collect()` чистые.

### 02.10.2026 — PHASE 2.0-3: Jobs subsystem — **DONE**

- `core/jobs.py` — менеджер задач (Архитектура §3.3/§5): пул
  worker-тредов (2, daemon, ленивый старт) + очередь; состояния
  `queued → running → completed|failed|cancelled`; поля id/type/meta/
  status/progress/queued_at/started_at/finished_at/logs/result/error/
  cancelable; module-API `init/submit/get/list/cancel/wait`.
- Хранение — sqlite-таблица `jobs` в devices.db: DDL (`JOBS_DDL`) живёт
  в `core/jobs.py`, `ensure_jobs_table()` вызывается из
  `_ensure_extra_tables`; персист best-effort при каждом изменении
  (память авторитетна, БД — история для `GET /api/jobs` после рестарта).
- Retention как у events (`events.retention_days`): `cleanup_old_jobs`
  в `init_db_schema` + ежесуточный `retention_loop` (daemon-тред).
- Recover: `recover_interrupted()` в `init_db_schema` и при первом
  обращении менеджера — queued/running, оборванные рестартом панели, →
  failed («прервано перезапуском панели»).
- Отмена cooperative: задача зовёт `ctx.check_cancel()` между шагами;
  queued-задача отменяется мгновенно; не-cancelable → `ok:false`
  (HTTP 409). Честность: nmap/apt прервать изнутри нельзя —
  `network-scan`/`module-catalog-*` cancelable=false, `module-install`
  cancelable=true (между шагами).
- API — новые URL (аддитивно к 1.1): `GET /api/jobs` (limit/status +
  active), `GET /api/jobs/<id>` (для поллинга), `POST /api/jobs/<id>/
  cancel` (admin).
- UI — виджет в `base.html` (правый нижний угол): poll `/api/jobs` 4 с,
  progress-бары, «отменить» (admin, Jinja-гейт), строка-итог на 6 с
  после завершения; CSRF-заголовок как у остальных POST.
- Миграции:
  - `POST /modules/<mid>/install` → job `module-install` (cancelable):
    `_install_manifest(m, ctx)` — лог/прогресс/отмена между шагами
    apt/pip/dirs/services; `record_install_result`/`set_module_status` —
    внутри задачи; каталог install/update → job `module-catalog-install/
    update` (CatalogError теперь в job.failed, а не в redirect err).
  - `POST /api/scan` → job `network-scan`: ответ `{ok, job}` (аддитивно;
    devices/stats/subnet — в job.result; nmap-ошибка — failed вместо
    503); `templates/devices.html` ждёт завершения через
    `GET /api/jobs/<id>` перед reload; фоновый `scan_loop` — тредом
    как был, мигрируем только ручной скан.
  - `POST /system/db-backup` → job `db-backup`: `systemctl start`
    с ожиданием oneshot вместо Popen; `/api/backup-status` и JS-поллинг
    не менялись (аддитивно добавлен `job` в ответ).
  - **db-restore НЕ мигрирован**: внутри — `systemctl stop
    lan-discovery` убивает процесс-исполнитель задачи; нужен внешний
    executor (решение/ТЗ отдельно).
- Тесты `tests/unit/test_jobs.py` — 25 кейсов: lifecycle/failed,
  cancel queued/running/not-cancelable/terminal/unknown, persist в
  sqlite, list (БД-история + память), recover, retention (в т.ч.
  days=0), роуты (shape/get/404/cancel/auth/bad-status), миграции
  install/catalog/scan/db-backup, контракт widget-разметки.
- Локально (Windows): 25/25 новых + полный юнит-набор 234 passed
  (2 фейла `test_api_disks_shape`/`test_help_facts_structure` —
  pre-existing, падают и на чистом HEAD `2542f55`: нет lsblk и
  linux-специфики); py_compile всех правок, рантайм-смоук core.jobs,
  Jinja-парс шаблонов — чисто; unit CI — после push этой фазы.

### 02.10.2026 — PHASE 2.0-4: Module manifest 2.0 + permissions + trust — **DONE**

- `core/manifest.py` — контракт манифеста 2.0 (спека §10–11): замкнутая
  сетка прав из 12 ключей (`network.read/configure`, `storage.read/write`,
  `services.read/control`, `process.execute`, `camera.read/control`,
  `usb/gpio/serial.access`) — подписи+описания для UI;
  `validate_manifest()` (проверяются только присутствующие поля —
  v1-манифесты валидны); `parse_version`/`core_version_ok`
  (подравнивание 1.2 ≈ 1.2.0); `install_confirm_text()` — текст
  UI-запроса прав.
- Права — строго по сетке: v1-примеры «admin/network» из docstring не
  использовались ни в одном из 33 существующих `module.json` →
  переопределяем семантику; валидация гоняется в catalog-путях и в
  тестах, локальные манифесты на discover не роняем (не ломаем рабочие
  каталоги опечатками).
- `compute_status()` — аддитивно (старые манифесты ведут себя как
  раньше): `min/max_core_version` против `APP_VERSION` → incompatible
  (fail-closed на нечитаемой заявленной версии; ctx без app_version →
  пропуск); `capabilities` вида `group.key` → requires-hardware (голые
  группы не проверяем — не гадаем); `dependencies` (id модулей) →
  requires-dependency, пока хоть одна не установлена или выключена;
  приоритет incompatible → requires-hardware → requires-dependency
  сохранён. `status_context()` отдаёт `app_version` (ленивый импорт app).
- Trust каталога (спека §12): SHA-256 тарболла из `index.json` сверяется
  всегда, когда там есть; `require_sha256` (settings) — opt-in
  fail-closed для legacy-index; `trusted_publishers` — opt-in fail-closed
  (непустой: чужой **или отсутствующий** publisher → отказ);
  `min/max_core_version` — дважды: index до скачивания и module.json
  после распаковки (авторитетно); publisher index ↔ module.json обязан
  совпадать — identity должен быть и в самом манифесте (отказ даже без
  trusted_publishers); id в module.json ↔ id из каталога. Результат
  фиксируется в state: `entry.publisher`, `entry.sha256`.
- **sha256 — не поле module.json**: манифест не может верифицировать сам
  себя; контрольная сумма живёт в index.json (спека-дерево Module
  читается как «версия манифеста хранит ожидаемый digest из index»).
- UI: confirm-запрос прав перед установкой (deps-install и
  catalog-install) — «Модуль «…» запрашивает права: … Продолжить?»
  (`|tojson` в атрибуте onsubmit); подписи прав в карточках
  (label + tooltip «key — описание»); catalog-карточка показывает
  `permissions` из index, если он их отдаёт.
- Schema-only (есть валидации, нет поведения — потребитель в Roles
  2.0-5): `conflicts`, `services`, `configuration`, `role_support`;
  `capabilities` голыми группами. Запрет одновременного включения
  conflicts — не вводим (нужен потребитель-применяющий). Sandbox/PKI —
  нет (спека §11–12: сначала permission model как контракт).
- Тесты +35: `test_manifest_v2.py` (19: сетка/валидация/версии/статусы/
  confirm, рендер /modules позитив+негатив),
  `test_module_catalog_trust.py` (16: sha256 match/mismatch/require,
  publishers fail-closed/allowed, min/max-core из index и манифеста,
  битый JSON, id/publisher-mismatch, флаг update). Локально: 269 passed
  (+2 pre-existing Windows-фейла); CI — после push.
- Побочный фикс `core/jobs.py`: гонка persist ↔ `ev.set()` — `wait()`
  возвращался до записи в sqlite (read-after-wait видел `running`,
  поймано ретраем `test_job_persisted_to_sqlite`); persist теперь до
  события (`_run_one`, отмена queued).

### 02.10.2026 — PHASE 2.0-5: Roles 2.0 — **DONE**

- `roles/*.json` — 9 манифестов (спека §14): legacy-три профиля
  1.1 (`default`/`media`/`network`) перенесены из кода PROFILES без
  изменений списков + 6 ролей из спеки: network-gateway, home-server,
  remote-site, industrial-gateway, network-diagnostic-box,
  camera-gateway (SDR — ждём хвост спеки). Поля контракта
  §14 присутствуют: required_modules/optional_modules/capabilities/
  hardware_requirements/dependencies/conflicts/recommended_configuration/
  security_profile.
- `core/roles.py` переписан на загрузчик: `validate_role()` (id==имя
  файла, типы полей, `"*"` только в required, conflicts ∩ ALWAYS_ON →
  ошибка), `load_roles()` (кэш 5 с, невалидные роли не показываются),
  `get_role`; PROFILES удалён (контракт роутов не менялся:
  `roles_overview()`/`apply_role()`/`active_role()` — имена и JSON
  ключи 1.1 сохранены, overview аддитивно расширен).
- Готовность роли — `role_blockers(rid, ctx)`: архитектура,
  hardware_requirements.{tools,storage}, capabilities `group.key`
  (голые группы не проверяем — не гадаем), dependencies.apt через
  `_missing_apt_packages`, dependencies.services через
  `core.services.status` (unknown → не блокируем). Применение роли
  с блокерами → `{ok: False, error}` (apply отказывает честно).
- `_targets()`: ALWAYS_ON > conflicts > членство в роли — конфликтный
  модуль выключается, но ALWAYS_ON неприкосновенен (валидация запрещает
  conflicts∩ALWAYS_ON на уровне манифеста).
- `roles_overview()` аддитивно: `required/optional/missing/
  not_installed/conflicts/blockers/ready/security_profile/
  recommended_configuration`, члены modules получают `role`
  (required/optional/conflict/always); старые ключи (active, roles[],
  modules[].id/name/always_on/status/enabled) не тронуты.
  missing = id ролей, которых ещё нет в панели (честный контракт:
  спека-роли ссылаются на будущие модули — apply их просто не трогает).
- Честность контрактов: `security_profile` ("standard"/"hardened") —
  бейдж в UI, enforcement нет; `recommended_configuration` — подсказка
  в UI, автоматически не применяется; `optional_modules` у спека-ролей
  пуст (спека не разделяет список на required/optional — весь список в
  required, наполнение optional — по мере появления реальных модулей);
  capabilities ролей: только явные (industrial-gateway →
  hardware.rs485, network-gateway/diagnostic → network.ethernet),
  hardware_requirements пусты (не выдумываем).
- UI `/roles`: блокеры роли (кнопка Применить disabled + причины),
  бейджи ready/security_profile, чипы модулей с роль-типом
  (★ always, ✕ conflict, title required/optional), «нет в панели» и
  «не установлены», «Рекомендуется: k=v»; сноска про PROFILES заменена
  на roles/*.json.
- Тесты `test_roles.py`: 12 → 19 (валидация ok/ошибки, load_roles
  скипает невалидные, blockers по каждому типу + не-гадание unknown,
  apply с блокерами отказывает, targets с conflicts, флаги role в
  overview, контракт аддитивных ключей; legacy-тесты переведены с
  PROFILES на load_roles). Локально: 276 passed (+2 pre-existing
  Windows-фейла); CI — после push.

### 02.10.2026 — PHASE 2.0-6: Network Core (транзакционный) — **DONE**

- Спека §5–6: `core/network.py` расширен до Core Network Manager (контракт
  `physical_ifaces` из 2.0-2 не менялся — потребитель capabilities
  сохранён): read-only объектный API `list_interfaces()` (`ip -j link` +
  sysfs-классификация wired/wifi/virtual; недоступный источник → None,
  вызывающий отвечает unknown), `list_addresses()` (`ip -j addr`),
  `list_routes()` (`ip -j route`, main table); перенос wifi-scan —
  `_parse_iw_scan()` (парсер построчно сохранён, контракт 1.1 включая
  «iw отработал, но пусто» → `ok: True, networks: []`) + `wifi_scan(ifaces)`.
- Транзакционный каркас (спека §6) `Transaction`: `add(label, apply,
  prepare=None, verify=None, rollback=None)` → `run()` = Prepare (снимки
  ДО любых изменений; провал prepare → ничего не применялось) → Apply +
  Verify пошагово → Commit; провал apply/verify → Rollback применённых
  шагов в обратном порядке. Конвенция: True/None — успех,
  False/исключение — провал; prepare возвращает rollback-данные (False
  зарезервирован под провал); шаг без rollback → `rolled_back: False`
  (честно о неполноте); ошибки rollback не затирают первичную ошибку
  apply/verify. Результат — dict с phase/rolled_back/steps/log,
  исключений наружу нет.
- Insurance (авто-rollback по таймауту/неверификации): `schedule_rollback(
  check_fn, grace)` — daemon-тред через grace сек проверяет `check_fn()`,
  при провале откатывает сохранённые rollback-данные; результат в
  `tx.insurance`/`tx.insurance_done` (тестируемо).
- Первые операторы-мутации (сетевые операции переезжают из модулей в
  Core): `set_address/del_address` (ip addr replace/del + verify по
  снимку; rollback = flush + восстановление снимка с scope; динамические
  адреса восстанавливаются статическими — осознанный компромисс ради
  доступности) и `set_route/del_route` (ip route replace/del, dst
  «default»/нормализованный network, хотя бы gateway/dev, rollback =
  flush dst + restore снимка main table). Валидация ДО транзакции: имя
  интерфейса ≤15 символов, CIDR/dst/gateway/metric — строгие форматы,
  argv без shell. DHCP/DNS/VPN/AP/bridge и firewall — по мере
  модулей-потребителей (firewall требует модели nft — отдельно).
- Миграция по плану Инвентаризации: nettools ping/dns/trace →
  `core.process.run` (JSON-контракты роутов не менялись), wifi-scan →
  `core.network.wifi_scan`; новые роуты аддитивно: `GET /api/network/info`
  (login) и `POST /api/network/address` (admin, `insurance_grace`
  клампится 0..300).
- UI sys-network block (admin): предупреждение «Эта операция может
  разорвать текущее подключение (This operation may disconnect the
  current session)» — в карточке и в confirm перед отправкой; выбор
  интерфейса/CIDR, статус фазы транзакции, авто-проверка через 20 с.
- Тесты `test_network_core.py` — 27: объекты/парсеры (классификация,
  iw-поля+сортировка, контракты wifi_scan), валидация входа, каркас
  (happy/prepare-fail/apply-fail-обратный-откат/verify-fail/исключения/
  без rollback/пустая), insurance (провал→откат, ок, выключено),
  операторы (валидация до чтения состояния, снимок в rollback,
  del-контракты, маршрутные счётчики prepare/verify), роуты
  (info-контракт, admin 403/400/clamp grace, делегирование wifi/
  nettools). Локально: 303 passed (+2 pre-existing Windows-фейла,
  3 Linux-skip); CI — после push.

### 02.10.2026 — PHASE 2.0-7: Storage Core — **DONE**

- Спека §7: `core/storage.py` — `ROOTS` (media/data/backup) + `path(root,
  *parts)` (posixpath — пути POSIX, локальный прогон на Windows не ломает;
  неизвестный корень → ValueError, прецедент services.control). Единственный
  источник путей для модулей.
- Миграция констант (значения НЕ изменились — 1.1-совместимость):
  `IPTV_DIR` (app.py, media_routes, system_routes), `MEDIA_DIR`,
  `PLAYLISTS_DIR` → `storage.path(...)`; дублей строк больше нет
  (тест-инвариант: app == media == system == path()).
- Backup targets: `DB_BACKUP_DIR` (`/srv/backup-db`) и `EMMC_BACKUP_PATH`
  (`/srv/backup-system/emmc.img.zst`) переопределены в storage;
  значения сохранены — их читают recovery.sh/restore_server/systemd-юниты,
  живущие за границей репо; миграция путей под `/srv/backup` без переноса
  данных отложена (legacy вне корней, честно записано в §3.7).
  system_routes: 3 хардкода emmc-пути → `core_storage.EMMC_BACKUP_PATH`;
  консистентность закрыта тестами (restore_server.py / deploy/backup-db.sh
  / recovery.sh сверяются со storage).
- `clone_disk(src, dst, on_progress, should_continue)` — перенос do_clone
  (Инвентаризация: 2.0-7): sync до/после, Popen dd, watcher-тред по
  /proc/<pid>/io с процентами 5..95 (как 1.1), cooperative-отмена через
  should_continue (сам dd убивает внешний cancel-путь), таймаут → kill;
  `_dd_progress_watcher` удалён из system_routes, роут clone вызывает
  core API. Исключений наружу нет — `{ok, error}`.
- Честность состава: disks/partitions/mounts/SMART уже абстрагированы в
  2.0-2 (`lsblk_text/df_text/smart_report`, потребитель `/api/disks`) —
  structured-JSON API отложена до потребителя (инвариант №5); shares —
  management guest-шары уже в `core/samba_guest.py`, list-shares по мере
  UI-потребителя; `ensure_roots()` не делаем — создание корней при первом
  реальном потребителе (сейчас makedirs делают сами модули под свои
  подкаталоги).
- Тесты `test_storage_core.py` — 16: корни/path/ValueError, единственный
  источник констант, консистентность бэкап-путей по файлам репо,
  миграция clone (grep-инварианты), clone_disk (валидация устройств,
  happy/error/timeout-kill/исключение, шкала процентов, watcher:
  cancel-выход без задержки, пропуск без total). Локально: 319 passed
  (+2 pre-existing Windows-фейла, 3 Linux-skip); CI — после push.

### 02.10.2026 — Внефазная доводка Инвентаризации: переносы строк 47–56 — **DONE**

- Работа между фазами (вне таблицы фаз): закрыты отложенные переносы
  из docs/Инвентаризация-core-2.0.md, у которых уже есть потребители:
  `app.py` — `_cmd` и `check_internet` делегируют в
  `core.process.out/run` (единая точка для всех роутов, shell-авто как
  в 1.1, UTF-8 с заменой вместо locale); `core_routes._cmd` →
  `process.out` (timeout 5); `monitor._cmd` → `process.out`
  (timeout 10); `core/module_loader` — dpkg-query batch → `process.run`;
  `core/discovery` — nmap `run_scan` → `process.run`;
  `core/samba_guest` — `smbcontrol`/`testparm` → `process.run`,
  fallback `systemctl reload smbd` → `services.control("smbd", "reload")`
  (в `ACTIONS` добавлен `reload` — единственный новый элемент контракта
  services, расширение аддитивно); `network_routes._bt_cmd` (bluetoothctl
  stdin) → `core_process.run(input=...)`.
- Семантика не менялась: stdout.strip()/"" на ошибке (out), rc-проверки и
  исключения (OSError/TimeoutExpired) — те же; `subprocess`-импорты из
  мигрированных файлов удалены (кроме samba_guest — нужен класс
  TimeoutExpired в `except`).
- Тесты: `test_discovery` патчат `d.process.run` (5 мест) вместо
  `d.subprocess.run`; без новых тестов — контракты не изменились.
  Локально: 319 passed (2 pre-existing Windows-фейла, 3 Linux-skip).
- Остались «отложено» (по мере потребителей/слоёв): inventory.py HTTP-скан,
  media_routes (journalctl/curl, Popen-проигрыватели — вне core),
  module_manager `_run`, system_routes статусы (→ core.services.logs);
  samba-guest management уже в core/samba_guest (inventory строка 50).
  Cherry-pick `1aa568f` (docs AGENTS с 1.1) не нужен — AGENTS 2.0
  переписан в 2.0-0, правка описывает этот репозиторий снаружи.
### 02.10.2026 — PHASE 2.0-8 (хвост спеки) + PHASE 2.0-9 (Architecture Audit) — **DONE**

- **2.0-8:** от заказчика получен хвост спецификации §15–37 (device
  identity, events, automation, hardware modules, API, config/secrets,
  UI 2.0/appliance, API-UI separation, installer, update/rollback, smoke,
  demo, documentation, portability, что не делать, compat, план фаз 0–10,
  versioning, DoD, требования к работе, первый результат). Дописан в
  docs/Спецификация-2.0.md (теперь §0–37 целиком, 1486 строк); закрыт
  пустой раздел `## SDR` блоком SDR Gateway; архитектура §3.8
  обновлена.
- **2.0-9 (§37 Phase 0):** read-only аудит репозитория →
  docs/2.0/ARCHITECTURE_AUDIT.md (A–O). Ключевые находки: AST-граф
  (core→app ×5, core→modules ×3, modules→app ×9 — инверсии; modules→core
  ×21 — норма); владение схемой БД в modules/devices_routes (devices.ip
  PK — идентичность = IP, §15-разрыв); события только от discovery
  (NEW/ONLINE/OFFLINE/MAC_CHANGED/IP_CHANGED) без namespace §16;
  4 разных сигнатуры register_routes; 33 builtin-манифеста legacy (0 с
  version); два UI-shell (base.html/base_app.html); 189 роутов; тесты
  324 unit + 15 live; install/update/recovery — app-level rollback есть,
  system-level нет (§26).
- **Изменение состава:** таблица фаз дополнена строками 2.0-10…2.0-21 по
  плану §O аудита (mapping §33 на текущий статус: закрыты Ph.0, Ph.2,
  Ph.5, Ph.6, частично Ph.1/3/4; следующая — 2.0-10 Core independence,
  она снимает инверсии и даёт core/db+core/version опорой для
  identity/events/automation). Порядок и границы фаз фиксирует аудит;
  каждая фаза — один контракт, тесты зелёные, аддитивность §32.
### 02.10.2026 — PHASE 2.0-10: Core independence — **DONE**

- core/version.py — единственный источник `APP_VERSION=2.0.0` (§34):
  app.py реэкспортирует (тесты/health), module_catalog и module_loader
  импортируют core.version напрямую (было `from app import APP_VERSION`).
- core/db.py — владение схемой sqlite (§19): DB-путь, SCHEMA_VERSION,
  миграции _migration_v1/v2 + MIGRATIONS (PRAGMA user_version),
  _ensure_extra_tables (9 серверных таблиц + jobs), _retention_days
  (через core.config), init_db_schema, get_db — перенесено из
  modules/devices_routes без изменения поведения (перенос строк,
  компиляция, тесты миграций).
- **Инверсии core→app/modules = 0** (было 8): discovery/events/jobs
  (get_db, DB, _cfg) → core.db/core.config; module_loader/catalog
  → core.version. `rg 'from app import|from modules' core/` — пусто.
- app.DB / devices_routes.DB / init_db_schema / get_db — реэкспорт
  (compat §32): inventory_routes (`from app import DB`), app-роуты,
  conftest и тесты продолжают работать; тесты миграций/фикстура devices_db
  переведены на прямой импорт `core.db` (патчи DB/_init_done теперь
  бьют в владельца схемы), retention-тест патчит `core.config.load`.
- `_cfg_net`/`retention_loop`/`_resolve_retention` читают настройки
  через `core.config.get` (семантика идентична app._cfg: тот же файл,
  TTL 10 с); потребители, патчащие `d._cfg_net`/`self._retention_cfg`,
  не тронуты.
- modules→app (9) и uniform register-контекст — остаются на 2.0-15
  (граница фазы: только core-инверсии, без контракта модулей).
- Тесты: 319 passed локально (2 pre-existing Windows-фейла, 3 Linux-skip);
  CI — после push (ожидание 324).

### 02.10.2026 — PHASE 2.0-11: Device identity — **DONE**

- core/identity.py — `derive_id(mac, ip)` (``mac:<lower(mac)>`` при известном
  MAC, иначе ``ip:<ip>``), `record_ip` (INSERT пары в ip_history + touch
  last_seen), `identity_of(con, ip)` — read-API для роута. device_id
  присваивается один раз (COALESCE) и НЕ переписывается при смене MAC
  (§15: device_id первичен, MAC-смена остаётся событием MAC_CHANGED).
- core/db.py — миграция v3: `MIGRATIONS` + `(3, _migration_v3)`,
  SCHEMA_VERSION=3; ALTER devices + device_id, таблица ip_history +
  индекс idx_ip_history_device, backfill (mac:/ip: + все пары истории);
  идемпотентна (повторный прогон без дублей).
- core/discovery.py reconcile — device_id во всех трёх путях:
  fresh (INSERT с id), moved (наследует device_id прежней строки —
  identity P5 сохранён), existing (COALESCE-присвоение); record_ip при
  каждом подтверждении онлайн.
- modules/devices_routes — аддитивный `GET /api/device/<ip>/identity`
  (login): {ok, device_id, ip, mac, hostname, ip_history[]} / 404.
- Сознательно вне границы фазы: devices.ip PK не менялся (§32 — слой
  поверх старой таблицы, UI /device/<ip> не тронуты), event-модель не
  тронута (namespace device./network./… — PHASE 2.0-12, §16).
- Тесты: +6 (миграция v3 backfill/идемпотентность/user_version=3;
  identity fresh/fallback/stable-on-mac-change/move-inherits;
  API shape+404). Локально 325 passed (2 pre-existing Windows-фейла,
  3 Linux-skip); CI — после push (ожидание 330).


### 02.10.2026 — PHASE 2.0-12: Events 2.0 — **DONE**

- core/events.py — §16: `NAMESPACE_EVENTS` (20 канонических имён
  device./network./storage./camera./job./module./system. — строгие для
  emit, severity в той же карте, видны и старому add_event);
  `LEGACY_ALIASES` NEW⇄device.new … IP_CHANGED⇄device.ip_changed;
  `emit()` (INSERT + fan-out, persist best-effort: ошибка БД/подписчика
  логируется, писатель не падает, con= → core.db.get_db);
  `subscribe()` → unsubscribe, `_notify` глотает ошибки подписчиков
  (фундамент Automation §17); dual-read в `list_events` — фильтр event
  строит варианты legacy⇄namespace (лента/UI-фильтры не ломаются).
- core/jobs.py — потребитель: `job.started` (старт), финальные
  `job.completed/failed/cancelled` (metadata job_id/job_type/error),
  `job.cancelled` в cancel-queued; **исправлена гонка read-after-wait**:
  `wait()` возвращал терминальный статус из памяти раньше persist/emit —
  введён `_finalized` (Task возвращается только терминальный+финализированный;
  строка из БД считается финализированной) — раньше тесты/подписчики могли
  не увидеть событие (флаки).
- Тесты: +11 (test_events ×8: strict-name, severity, persist,
  subscribe/unsubscribe, payload shape, сломанный подписчик, dual-read;
  test_jobs ×3: пишутся job.started/completed, failed→critical,
  подписчик видит жизненный цикл; фикстура jobs_db патчит core.db.DB →
  tmp). Локально 336 passed (2 pre-existing Windows-фейла, 3 Linux-skip),
  стабильность гонки — 3 прогона jobs/events ×53 passed; CI — после push
  (ожидание 341).


### 02.10.2026 — PHASE 2.0-13: Automation — **DONE**

- core/automation.py — §17 Event→Rule→Action, компактный appliance
  engine: правило {name, enabled, event, actions[], cooldown_sec} в
  automation_rules (DDL в модуле + ensure через core.db, паттерн
  JOBS_DDL); validate_rule (имя события из NAMESPACE_EVENTS или алиаса,
  actions — только зарегистрированные типы, cooldown >= 0);
  list/get/add/delete/set_enabled.
- Матчинг: dual-read через events._event_variants — правило "OFFLINE"
  ловит device.offline и обратно; cooldown в памяти (_last_fired),
  fired_count/last_fired_at в БД; guard от петель: metadata.automation
  → handle_event выходит (цепочки правил §17 — вне объёма).
- Действия: реестр register_action (log — в лог панели, event — новое
  namespace-событие source=automation с metadata {automation, trigger});
  ошибки действия логируются, engine не падает; модули добавляют свои
  типы через register_action.
- Интеграция: engine — подписчик events.subscribe (start/stop
  идемпотентны), automation.start() в __main__ app.py рядом с
  retention_loop; роуты modules/automation_routes.py: страница
  /automation (login, render_template) + API list/create/toggle/delete
  (can_edit на мутациях); пункт CORE_NAV «Automation» /automation
  (Система, admin, order 86); шаблон templates/automation.html (форма +
  таблица, vanilla JS, CSRF авточерез обёртку fetch base.html).
- Тесты: +11 (CRUD-круг, валидация ×7, матчинг/cooldown/disabled,
  event-action+guard, start-stop подписка, API CRUD+404/400, auth,
  страница). Локально 347 passed (2 pre-existing Windows-фейла,
  3 Linux-skip); CI — после push (ожидание 352).


### 02.10.2026 — PHASE 2.0-14: Config & Secrets — **DONE**

- core/secrets.py — §21: KV API get/set/delete/keys поверх отдельного
  файла secrets/kv.json (Fernet at rest, chmod 600, атомарная запись
  tmp+fsync+replace; default только для отсутствующего ключа, битое
  значение → лог + default); crypto (load_key/encrypt/decrypt + legacy
  base64(HMAC||text) fallback) перенесён из modules/core_routes БЕЗ
  смены формата — UI-менеджер /apps/passwords продолжает работать;
  core_routes оставил compat-алиасы _secrets_encrypt/_secrets_decrypt
  и имена SECRETS_DIR/SECRETS_KEY_PATH (§32), своё состояние — только
  _enc_ids (миграция записей без двойного шифрования).
- core/config.py — §20: docstring «Классы конфигов» (Core/Secrets/
  Module/Role/Runtime state) + единый справочник путей
  MODULES_STATE_PATH/ROLES_STATE_PATH; алиасы: module_loader.STATE_PATH,
  roles.STATE_PATH, module_catalog.SETTINGS_PATH — берутся отсюда
  (единственный источник).
- core/module_catalog.py — потребитель §21: token каталога берётся из
  secrets.get("modules_catalog_token"), fallback на legacy-место
  settings["modules_catalog"]["token"] (§32, аддитивно).
- Тесты: +10 (test_secrets.py: KV roundtrip/перезапись/deletion, at-rest
  шифрование + 0600-ключ, coerce str, encrypt/decrypt/битое-исключение,
  legacy HMAC + wrong key, fallback/приоритет токена, справочник путей).
  Локально 357 passed (2 pre-existing Windows-фейла, 3 Linux-skip);
  CI — после push (ожидание 362).


### 02.10.2026 — PHASE 2.0-15: Module contract v2 — **DONE**

- app.py (assembly root): блок Uniform module context — ctx =
  SimpleNamespace(login_required/admin_required/can_edit,
  get_current_user/get_current_username, page_data, _cmd, _cfg, DB,
  GAMES_DIR, _SERVICE_START, panel_name, check_internet_cached,
  weather_current) + ctx.service_state после import system_routes;
  все 12 вызовов register_*(app, ctx); register_auth перенесён после
  сборки ctx.
- Модули: единая сигнатура register_routes(app, ctx) во всех 12
  (auth, module_manager, devices, jobs, automation, weather, system,
  network, media, monitoring, inventory, core) — деструктуризация
  имен в начале вместо параметров; ленивые from app import в
  обработчках (devices ×4, weather, monitoring, inventory, system ×3,
  core ×2, auth) заменены на ctx-замыкания; module_manager больше не
  импортирует get_current_user из auth; core_routes: page_data
  стала ctx-заменой (модульная делегация удалена, внешних
  вызывающих не было), SETTINGS_PATH → core.config; system_routes:
  APP_VERSION → core.version (убран __import__("app")).
- Инверсии modules→app = 0 (было ×9+): inventory/monitor/
  devices._current_subnet читают настройки через core.config.get
  (стиль _cfg, единый кэш 10 с).
- Манифесты: 33 builtin module.json → version 2.0.0 (+ capabilities
  у 6 hardware-зависимых: camera/radio/wifianalyzer/disks/sys-emmc/
  player); валидация validate_manifest чистая; шаблон /modules
  показывает v2.0.0 вместо Unknown.
- Тесты: +3 (tests/unit/test_module_contract.py: uniform-сигнатура
  всех register_routes, version+validate 33 манифестов, capabilities);
  test_module_status обновлён под version-бейджи. Локально 360
  passed (2 pre-existing Windows-фейла, 3 Linux-skip); CI после push
  (ожидание 365).


### 02.10.2026 — PHASE 2.0-16: System rollback — **DONE**

- core/syschange.py (§26): конвейер preflight→backup→apply→verify
  →rollback для apt_install/file_write; run_cmd инжектируемый
  (тесты/стенд), лог шагов → job; бэкапы в
  /var/lib/lan-discovery/rollback/<id>/ (manifest.json, dpkg-до,
  selections.txt, files/); публичный rollback(txn_dir) идемпотентен
  (для ручного отката и смоука §27); SystemChangeError несёт .txn
  (state: preflight-failed/backup-failed/rolled_back/rollback-failed).
- Потребитель: apt-шаг module_manager._install_manifest переведён на
  syschange.run (логи preflight/backup/apply/verify в job, сбой →
  ok: false + откат частично установленных пакетов); format шагов
  "apt install X: OK/FAIL" сохранён.
- Разделение §26 соблюдено: application rollback — в update.sh;
  здесь только system-level; docs это фиксируют.
- Тесты: +9 (tests/unit/test_syschange.py, FakeApt: порядок шагов,
  откат на preflight/apply/verify, снятие новых пакетов,
  file_write+ручной rollback, потребитель ok/fail). Локально 369
  passed (2 pre-existing Windows-фейла, 3 Linux-skip); CI после
  push (ожидание 374).

### 03.10.2026 — PHASE 2.0-17: Appliance smoke test — **DONE**

- tests/unit/test_appliance_smoke.py: единая цепочка §27 в одном
  тесте (12 шагов): чистая установка → login → hardware detection
  (/api/health, db.user_version=3, платформа 7 ключей) →
  capabilities → network discovery (fake run_scan → job) → module
  installation (notes, state в tmp) → role application (default,
  ok:true) → jobs (install completed) → configuration change
  (settings 45→30 с откатом) → версия из core.version (§34) →
  failure simulation (syschange.BACKUP_ROOT в tmp, FakeApt сбой,
  pkg-a не остался, модуль не помечен installed) → rollback
  (настройки прежние, health жив).
- tests/live/test_live_appliance_smoke.py: та же цепочка против
  живого стенда (LAN_PANEL_URL + маркер live, skip при
  user_version<3); apt/update.sh — вручную по §27/N6.
- tests/live/conftest.py: пароль админа из LAN_PANEL_PASS;
  tests/live/test_live_smoke.py: user_version in (2,3) — live-тесты
  работают и против 1.1 (X96), и против стенда 2.0.
- Фикс: module_manager._module_install_job — set_module_status
  только после проверки result.ok (раньше при сбое apt+rollback
  модуль всё равно помечался установленным).
- Изоляция unit-цепочки: патчи core.db (devices+events в tmp),
  jobs-manager, system_routes.DB (иначе health читает /opt),
  module_state/roles/settings → tmp, фейковые run_scan/reconcile,
  FakeApt на _run; выбор job — по дельте id до/после submit.
- Тесты: +1 unit (12 шагов цепочки). Локально 370 passed
  (2 pre-existing Windows-фейла, 3 Linux-skip); CI после push
  (ожидание 375).
### 03.10.2026 — PHASE 2.0-18: Installer 2.0 — **DONE**

- install.sh переведён на цепочку §25: preflight → hardware
  detection → dependencies → core (code/venv) → modules →
  configuration (config/db) → systemd → health check (main() — ровно
  этот порядок, тест его фиксирует).
- preflight 2.0: выбор python3/python >=3.9 (PY_BIN), arch/kernel,
  дистрибутив из /etc/os-release (Debian/Ubuntu/Armbian → ok, иное →
  WARN), apt/dpkg → WARN для не-Debian, место на диске (<400MB →
  WARN); без предположений о плате (§25).
- hardware detection: tools/hw_detect.py — переиспользует
  core/hardware.detect_platform() (тот же источник, что /api/health;
  только stdlib — работает до venv), отчёт $PREFIX/hw-detect.json
  (platform/дистрибутив/python/kernel); сбой → WARN, не критично.
- dependencies для minimal: критичные (python3-venv/cffi/cryptography/
  bcrypt) обязательны, опциональные (nmap/traceroute/dnsutils/iw/bluez/
  smartmontools/ffmpeg/mpv) — best-effort по одному; неудачи
  дописываются в hw-detect.json (deps_missing).
- modules: проверка discover_modules() (builtin-манифесты читаются,
  инвариант №5 — потребитель core.module_loader).
- health check без curl (minimal!): python3 urllib, порт из
  settings.json (web.flask_port), валидация JSON, версия +
  user_version (v<3 → WARN), до 60 с.
- CI: шаг "Installer 2.0 (bash -n + dry-run §25)" в ci.yml +
  py_compile tools/*.py; тесты: chain-порядок main(), dry-run (bash
  находит рабочий на Windows-стенде, иначе skip), hw-detect report.
- Тесты: +3 (tests/unit/test_installer.py). Локально 373 passed
  (2 pre-existing Windows-фейла, 3 Linux-skip); CI после push
  (ожидание 378).
### 03.10.2026 — PHASE 2.0-19: UI 2.0 shell — **DONE**

- Модель разделов §22 в core/module_loader: SECTIONS (HOME, NETWORK,
  MONITORING, STORAGE, APPLICATIONS, HARDWARE, SYSTEM, ADMIN) +
  section-поле у всех пунктов CORE_NAV (включая новый «Главная») +
  GROUP_TO_SECTION для модульных вкладок (tab.section — аддитивный
  override, §32); nav_items()/active_section()/nav_sections(),
  nav_groups(admin, section) — фильтр по разделу (без section — все
  группы, совместимость тестов §32).
- Application Shell в base.html: строка разделов (иконки, aria-
  current, responsive overflow-x) над доменными вкладками; tabs
  показывают только пункты активного раздела (shell_groups в
  context_processor + nav_sections/active_section).
- HOME = dashboard: новый роут "/" (core_routes.home_page) и
  templates/dashboard.html — 6 ответов §22 (всё ли нормально / что
  происходит / устройства / jobs / проблемы / предупреждения) через
  клиентский фетч /api/dashboard + /api/jobs с loading/error states
  (§23); devices переехал на /devices, после /login → "/" (HOME);
  /device/<ip> → секция NETWORK.
- STORAGE: роут "/"-уровня /storage (core_routes.storage_page) на
  read-only контрактах core.storage (roots/lsblk/df) — третий
  потребитель storage core; пустое состояние без lsblk/df (§23).
- §23 компоненты: window.toast() (toast-root, aria-live) +
  window.confirmDialog() (<dialog>); 3 legacy alert() в base.html
  заменены на toast (unified notifications); единые иконки разделов.
- Тесты: +12 (tests/unit/test_shell_20.py: модель §22, активные
  секции/группы, dashboard 6 ответов, /devices, /storage, компоненты);
  правки responsive/a11y под /devices (2 места). Локально 385 passed
  (2 pre-existing Windows-фейла, 3 Linux-skip); CI после push
  (ожидание 390).

### 03.10.2026 — PHASE 2.0-20: Portability — **DONE**

- §30-аудит плато-специфики в коде панели (core/, modules/,
  templates/, static/, app.py, install.sh): убраны имена плат и
  вендоров. sys-board: module.json name/title «X96 Max» → «Плата»
  (реальное имя платы и так рендерится из device-tree через
  board_title()); справка hf.lan — плато-подписи → generic
  («Основная панель», «Удалённый сервер», «Рабочая станция», …)
  + гейт по network.subnet: список хостов показывается только если
  primary в настроенной подсети, иначе таблица «Сетевые устройства»
  скрыта; monitoring: дефолт hosts → [] + empty state в
  monitoring.html (хосты — только из настроек); help.md модулей
  (monitoring, wifianalyzer); докстринги (app.panel_name,
  discovery._scan_enabled, system_routes.board_title, _help_facts).
- Инвариант закреплён тестом tests/unit/test_portability.py:
  grep-запрет 16 имён плат/вендоров в коде панели (tests/ и tools/ —
  исключение как инвентарь репозитория), generic-манифест sys-board,
  гейт hf.lan по subnet.
- Физические стенды (Orange Pi / X96 Max / x86_64) в фазе не
  использовались: инвариант §30 — «нет плато-ветвлений в коде» —
  доказан кодом+тестами (§36); живой прогон на железе — при стенде
  (N6, решение пользователя).
- Локально 388 passed (2 pre-existing Windows-фейла, 3 Linux-skip);
  CI после push (ожидание 393).

### 03.10.2026 — PHASE 2.0-21: Release — **DONE**

- Документация §29: 11 guides в docs/2.0 — ARCHITECTURE (целевая
  модель, границы Core/Module/Role/UI, инварианты слоёв, потоки),
  CORE_API (контракты всех subsystems core), MODULES (манифест v2,
  uniform register_routes(app, ctx), жизненный цикл), CAPABILITIES
  (11 групп, состояния/reliability, добавление hardware), ROLES
  (roles/*.json, apply/role_blockers), NETWORK (net.transaction,
  discovery/reconcile), STORAGE (ROOTS/path, потребители), JOBS
  (контракт, события job.*, кто использует), SECURITY (secrets,
  trust, syschange, границы), DEVELOPMENT (тесты/CI/процесс/
  инварианты), PORTING (чек-лист нового устройства, запрет
  if-board).
- §33 Ph.10 guides: INSTALL (цепочка Installer §25, флаги, чистый
  uninstall), UPGRADE (цикл update.sh бэкап→verify→авто-откат,
  application vs system rollback, миграции PRAGMA user_version),
  MIGRATION (1.1→2.0: что не меняется/меняется, порядок перехода,
  откат на 1.1 с бэкапом devices.db). RELEASE_NOTES.md — draft
  v2.0.0 (тег — по отдельному указанию; APP_VERSION=2.0.0 в
  core/version.py, §34).
- Demo mode §28: core/demo.py — API adapter (before_request
  первым в очереди): GET/HEAD /api/* → фикстуры <dir>/api/*.json
  (кэш 5 с), без фикстуры → безопасный {ok:false, demo:true} 404,
  изменяющие методы → 403, без сессии → 401; включение —
  settings.web.demo / LAN_DEMO=1, каталог — web.demo_dir /
  LAN_DEMO_DIR; контекст demo_mode → плашка .demo-banner в base.html
  (production HTML не копируется — §28). tools/make_demo.py
  --fixtures <dir> — режим снимка только GET-API с той же
  санитизацией (секреты/MAC/имена/подсеть), без HTML-этапов;
  статичный демо-сайт сохранён (§28 «сохранить»).
- Тесты: +7 (test_demo_adapter: fixture вместо реального роута,
  безопасный 404, read-only 403, 401 без сессии, плашка, demo off,
  загрузчик фикстур). Локально 395 passed; CI 400 passed
  (run 37081602972). Документация-пара: ARCHITECTURE/CORE_API +
  guides §29/Ph.10 (run 37080956726 — 393 passed).

**ROADMAP 2.0-0…2.0-21 закрыт.** Остаток: тег `v2.0.0` и публикация
Release — по отдельному указанию; живой прогон на платах — N6
(стенд решает пользователь).


### 03.10.2026 — Стенд N6: живой прогон 2.0 на X96 Max — **DONE**

- Стенд: X96 Max 192.168.3.243 — панель 2.0 параллельно с 1.1 (юнит
  lan-discovery-2, порт **8090**: 8080 — 1.1, 8081 — сервис
  восстановления, не трогаем). Изоляция — bind-монты в юните стенда:
  весь /opt/lan-discovery и весь /etc/lan-discovery перекрыты СВОИМ
  (/opt/lan-discovery-2.0 + etc-lan/: settings с портом 8090, свой
  users admin/1234, копии iptv); код — релиз 2.0.0 как есть, своя БД
  devices.db (миграции v1→v2→v3 применены при старте).
- Прогон: health (2.0.0 / user_version=3 / board X96 Max / capabilities
  = 12 / discovery жив), **live-смоук 16 passed** — цепочка §27:
  capabilities → install notes (job module-install completed) → roles
  default apply → settings change + откат → несуществующий модуль 404
  (state чист). Остаток N6 (apt-deps/update.sh вручную) — по указанию.
- Фикс в ходе прогона: live-тесты слали POST без CSRF → 400 (Flask-WTF
  CSRFProtect; панель отвечала корректно) — dmin_session отдаёт
  csrf_token, цепочка шлёт X-CSRFToken (8fe0f45); CI 400 passed.
- 1.1 не тронута: 8080 active, 1.1.0 / user_version=2, settings.json и
  users.json не менялись (запись изолирована bind-монтируемыми
  каталогами).
- Фикс по факту работы стенда: две панели на одном хосте делили куку
  session (куки не изолируются по порту) → вкладка 1.1 получала
  «The CSRF session token is missing». Имя куки стенда вынесено в
  settings.web.session_cookie (session_ld20, дефолт «session» без
  ключа не меняется); unit-тест +1, воспроизведение: обе панели в одном
  браузере дают 302 на логин (было 400).
- UX-догонка той же ловушки: мёртвая кука (восстановленная вкладка/кэш
  формы) давала голый 400 «The CSRF session token is missing» —
  errorhandler CSRF (79404b3): /login → свежая форма с «Форма устарела —
  войдите ещё раз» (200, юзер жмёт без F5), /api/* → JSON 400, прочее →
  текст с подсказкой F5; валидация токена не ослаблена. Тесты: unit +1
  (402 в CI), live-тест с версионным гейтом (1.1 → прежний 400).


### 03.10.2026 — FINAL BETA AUDIT: pre-release блокеры закрыты — **DONE**

- Аудит 18 этапов своими инструментами (подагенты упали на rate limit)
  выдал шесть классов находок; в код внесены все блокеры:
  **A-01** — install.sh `step_config` сидит users.json (admin/1234,
  bcrypt; идемпотентно, атомарно): чистая установка без входа больше
  невозможна; **C-02** — network_check.py возвращён в репо, путь от
  __file__ (не /opt-хардкод), вызовы sys.executable;
  **B-03** — discovery-события наконец доходят до Automation §17:
  add_event(out=) + notify_all ПОСЛЕ commit писателя (иначе подписчик
  automation писал в БД внутри чужой транзакции → busy/deadlock),
  payload — namespace-имя, БД — legacy (dual-read);
  **B-04** — @can_edit на 45 mutation-маршрутах (notes/inventory/media/
  network), guest-role получает 403 на запись, инвариант-тест сканирует
  modules/+app.py; **B-05** — sys.path app.py от __file__ (нет
  подхвата ядра 1.1 из чужого префикса); долг **D-07..D-11**
  (атомарный save_users, 401 JSON для /api без сессии, demo-барьер на
  не-GET HTML-путей кроме /login, skipif /proc для 2 windows-only
  тестов, ссылка APP_VERSION → core/version.py в CHANGELOG).
- Тесты: +16 (A-01, C-02, B-03 ×4, B-04 — инвариант + guest ×5,
  D-07, D-08, D-09, B-05); локально **413 passed / 5 skipped**
  (402 базовых + 16 новых; CI — 418 passed)
  (skipped — только Linux-гейты); существующие фикстуры под новый
  контракт починены (401 вместо 302 в смоуке, events_out в моках
  reconcile). Деплой на стенд N6 и live-прогон — следующий шаг.

- Деплой и живые пробы (N6): бэкапы 24 файлов в /root/prerelease-*
  -backup.tar.gz, архив 25 файлов распакован, py_compile + bash -n
  зелёные, юнит lan-discovery-2 перезапущен (health 2.0.0 /
  user_version=3). **Live 16 passed** — анонимный 302-контракт в
  двух тестах заменён на новый D-08 (401 + JSON). Пробы новых
  фиксов: аноним /api/status → 401 {"error": "unauthorized"};
  временный гость (probe_guest, восстановлен байт-в-байт после
  пробы): чтение 200, POST /api/nettools/ping → 403 forbidden
  (B-04); админ /api/network/check → валидный JSON
  {"internet": true, "ru_zone": true} (C-02, файл в репо и на
  стенде). Ловушка пробы: GET /api/network/config у гостя → 500 —
  load_network_config при отсутствии /etc/lan-discovery/network.json
  падал в NameError: _cfg (только в замыкании register_routes);
  фикс — дефолт без _cfg, unit +1 (414 passed), задеплоен после
  бэкапа modules/network_routes.py.

### 03.10.2026 — P2 Cleanup: Configuration Core, атомарность, префикс — **DONE**

- Аудит 7 подзадач по коду (не по промпту/README): закрыто 10 P2-хвостов
  + 3 новых. **Configuration Core** — один кэш/writer: `core/config`
  (адаптеры `load_settings/save_settings` в app/core_routes/weather
  делегируют, второй кэш и `open(..., "w")` удалены — инвариант-тест
  сканирует app/core/modules). **Префикс**: `core.config.PREFIX/DB_PATH`
  (env `LAN_PREFIX`) вместо хардкода `/opt/lan-discovery/devices.db`
  (core/db, currencies/inventory/recycling/system/weather, install.sh
  step_db); BindPaths стенда `/opt/lan-discovery-2.0:/opt/lan-discovery`
  даёт тот же путь БД внутри сервиса — данные не сдвинулись.
  **Атомарность**: `modules.json`/`roles.json` через `write_json_atomic`
  (tmp+fsync+os.replace, обломки tmp убираются, False наверх).
  **update.sh**: health-порт из `web.flask_port` (хардкод 8080 на стенде
  ловил чужую панель 1.1), флаг `--unit`, `network_check.py` в
  CODE_ITEMS, meta-версия из `core/version.py`; граница rollback/провал
  pip и семантика доставки событий (durable history + best-effort notify,
  без replay) зафиксированы в docs §3.11/Установка/UPGRADE и шапке
  events.py; sync_check — инвентарь корневого network_check.py и roles/
  (был ложный only_local); починен фикстур test_appliance_smoke под
  новый контракт.
- Тесты: +9 → **423 passed / 5 skipped** (torn-write core.config,
  инвариант единственного writer'a, prefix/DB, roles/module-state
  atomic+corrupt, no-replay/порядок батча, installer --prefix,
  update.sh --help/флаги). Деплой N6 с бэкапами
  (/root/p2cleanup-backup*-*.tar.gz, 21+7 файлов): py_compile + bash -n
  зелёные, сервис active, state-файлы валидны, .tmp = 0. **Live 16/16**
  + пробы 12/12 (settings GET→POST идентичен, roles apply, module
  toggle ×2, network config/check, аноним /api/settings → 401).
  sync_check `--prefix /opt/lan-discovery-2.0` → **exit 0** (exact=225);
  сервис 1.1 не тронут.

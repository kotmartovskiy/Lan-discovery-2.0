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
| **2.0-6** | Network Core (транзакционный) | `core/network.py`: объекты interfaces/addresses/routes/firewall/…; каркас Prepare→Apply→Verify→Commit/Rollback; UI-warning «This operation may disconnect the current session»; авто-rollback; первый перенос: sys-network-операции; DHCP/DNS/VPN/AP/bridge — по мере модулей-потребителей | pending |
| **2.0-7** | Storage Core | `core/storage.py`: корни `/srv/media|data|backup`, disks/partitions/mounts/SMART (lsblk/smartctl уже в TOOL_PROBES), shares/backup targets; `storage.path()` для модулей; миграция констант модулей (IPTV_DIR/MEDIA_DIR/PLAYLISTS_DIR и др.) | pending |
| **2.0-8** | Хвост спецификации | разделы после «## SDR» (§14+) от заказчика → дополнить `docs/Архитектура-2.0.md` и этот ROADMAP | ждём ТЗ |

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

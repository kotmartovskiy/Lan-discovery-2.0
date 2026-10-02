# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/). Версия панели —
`APP_VERSION` в `app.py` (показывается в `/api/health`, `/api/system/health`
и на странице «О системе»). Git-тег `vX.Y.Z` ставится на релиз.

## [2.0.0] — не выпущена (в разработке)

### Добавлено
- **Переносы Инвентаризации (внефазно, 02.10.2026):** `app._cmd`/`check_internet`, `core_routes._cmd`, `monitor._cmd`, dpkg-query batch, nmap `run_scan`, samba `smbcontrol`/`testparm`/`systemctl reload` (в `ACTIONS` добавлен `reload`), bluetoothctl `_bt_cmd` — все на `core.process`/`core.services` без смены семантики; удалены мёртвые `subprocess`-импорты. Остальные строки Инвентаризации — по мере потребителей.
- **Storage Core (PHASE 2.0-7):** `core/storage.py` — корни `ROOTS`
  (`/srv/media|/srv/data|/srv/backup`) + `path()` (posixpath, неизвестный
  корень → ValueError) как единственный источник путей; IPTV_DIR (app/
  media/system), MEDIA_DIR, PLAYLISTS_DIR переведены на `path()` — значения
  не изменились. Backup targets `DB_BACKUP_DIR`/`EMMC_BACKUP_PATH` в storage
  (legacy-значения сохранены — читают recovery.sh/restore_server/systemd
  за границей репо), хардкоды emmc из system_routes убраны, консистентность
  путей по файлам репо закрыта тестами. `clone_disk()` — перенос do_clone
  (dd + прогресс 5..95% по /proc, cooperative-отмена, таймаут → kill;
  `_dd_progress_watcher` удалён). Disks/SMART — read-only контракты 2.0-2;
  structured-JSON и list-shares — до потребителей. Тесты: 16.
- **Network Core + транзакции (PHASE 2.0-6):** `core/network.py` —
  read-only объектный API (`list_interfaces/list_addresses/list_routes`,
  недоступный источник → None → unknown, а не absent) и транзакционный
  каркас `Transaction` (спека §6: Prepare → Apply → Verify → Commit,
  провал → Rollback в обратном порядке; prepare-снимки = rollback-данные;
  шаг без rollback → `rolled_back: False`; исключений наружу нет) +
  insurance — отложенная страховка `schedule_rollback(check_fn, grace)`:
  через grace секунд авто-проверка и авто-rollback в daemon-треде.
  Операторы `set_address/del_address/set_route/del_route` (ip addr/route,
  валидация входа до транзакции, verify по снимку, restore с scope);
  DHCP/DNS/VPN/AP/bridge — по мере модулей-потребителей, firewall —
  отдельной фазой. Переносы по Инвентаризации: nettools ping/dns/trace →
  `core.process.run`, wifi-scan → `core.network.wifi_scan` (JSON-контракты
  роутов не менялись). Новые роуты: `GET /api/network/info` (login),
  `POST /api/network/address` (admin, grace 0..300). UI sys-network block —
  предупреждение «Эта операция может разорвать текущее подключение
  (This operation may disconnect the current session)» + форма
  интерфейса/CIDR. Тесты: `test_network_core.py` (27).
- **Roles 2.0 (PHASE 2.0-5):** роли переехали из кода PROFILES в
  манифесты `roles/<id>.json` (9 файлов: legacy default/media/network +
  6 ролей спеки §14 — Network Gateway, Home Server, Remote Site,
  Industrial Gateway, Network Diagnostic Box, Camera Gateway; SDR ждём);
  поля §14: `required_modules/optional_modules/capabilities/
  hardware_requirements/dependencies/conflicts/recommended_configuration/
  security_profile`; `core/roles.py` — `validate_role`/`load_roles`
  (невалидные роли не показываются), `role_blockers` (архитектура/железо/
  capabilities `group.key`/apt/services — роль с блокерами не
  применяется: `{ok:False, error}`), `_targets`: ALWAYS_ON > conflicts >
  членство; `roles_overview()` аддитивно: required/optional/missing/
  not_installed/conflicts/blockers/ready/security_profile/
  recommended_configuration + `role` у каждого модуля; apply/API-контракты
  1.1 не менялись (ALWAYS_ON сохранён); UI `/roles` — блокеры (кнопка
  disabled), бейджи ready/security_profile, ✕-конфликты, «нет в панели»,
  «Рекомендуется»; тесты 12 → 19.
- **Module manifest 2.0 + permissions + trust (PHASE 2.0-4):**
  `core/manifest.py` — сетка прав из 12 ключей спеки §11
  (`network.read/configure`, `storage.read/write`, `services.read/control`,
  `process.execute`, `camera.read/control`, `usb/gpio/serial.access`)
  с подписями для UI, `validate_manifest()` (v1-манифесты валидны),
  `core_version_ok()` (min/max_core_version против APP_VERSION),
  текст UI-запроса прав при установке; `compute_status()` проверяет
  min/max_core_version (→ incompatible), `capabilities` `group.key`
  (→ requires-hardware) и модульные `dependencies` (→ requires-dependency);
  каталог: SHA-256 тарболла из index.json (opt-in `require_sha256`),
  opt-in `trusted_publishers` (fail-closed), сверка publisher index ↔
  module.json и id, min/max_core_version до скачивания и после
  распаковки, `entry.publisher`/`entry.sha256` в state; UI `/modules` —
  confirm-запрос прав перед установкой (deps и catalog) + подписи прав
  в карточках; schema-only: `conflicts/services/configuration/role_support`
  (потребитель — Roles 2.0-5); +35 тестов
  (`test_manifest_v2.py`, `test_module_catalog_trust.py`).
- **Jobs subsystem (PHASE 2.0-3):** `core/jobs.py` — фоновые задачи
  (queued/running/completed/failed/cancelled): пул worker-тредов,
  cooperative-отмена (`ctx.check_cancel()`), логи/прогресс/результат;
  sqlite-таблица `jobs` в devices.db (retention как у events,
  recover после рестарта); новые роуты `GET /api/jobs`,
  `GET /api/jobs/<id>`, `POST /api/jobs/<id>/cancel` (аддитивно);
  виджет активных задач в UI (progress + отмена); на jobs мигрированы
  установка модулей (deps + каталог), ручной network scan (`{ok, job}`
  вместо синхронного ответа, JS ждёт завершения) и backup БД
  (systemctl с ожиданием вместо Popen); db-restore остался
  синхронным (рестарт панели убивает исполнителя задачи);
  +25 тестов (`test_jobs.py`).
- **Core skeleton (PHASE 2.0-2):** `core/process.py` (единый запуск
  команд: timeout, utf-8, `run`/`out`), `core/services.py` (facade
  systemd: status/control/health/logs — таймаут и ошибки без исключений),
  `core/config.py` (settings.json без app: кэш 10 с, атомарная запись);
  read-only-контракты `core/network.py` (`physical_ifaces`) и
  `core/storage.py` (`lsblk_text`/`df_text`/`smart_report`);
  `docs/Инвентаризация-core-2.0.md` — 71 вызов/13 файлов + план
  переноса; первый перенос 5 точек (статус служб, `/api/service/…`,
  `/api/disks`, сетевые интерфейсы capabilities, чтение settings) без
  изменения JSON-контрактов; +20 тестов (`test_core_layer.py`).
- **Capabilities 2.0 (PHASE 2.0-1):** `core/capabilities.py` — новые
  группы `network`/`hardware`/`radio`/`camera`/`media`/`service`
  (18 capability), в `storage` — `local`/`removable`/`smart`; только
  read-only пробы (sysfs/proc/dev/PATH): источник недоступен →
  unknown/unverified, пустой поиск → absent/detected, прочитанное
  содержимое → value+measured; SDR — по подтверждённым USB ID,
  subghz при живом SPI → unknown, docker CLI без сокета → unknown;
  `TOOL_PROBES` += `hostapd`, `docker`; UI `/capabilities` — секции
  групп (макрос `cap_row`); `/api/capabilities` аддитивно; +20 тестов.
- **Основа 2.0 (PHASE 2.0-0, 01.10.2026):** репозиторий выделен из
  `Lan-discovery-1.1` с полной историей (remote `upstream-11` для
  cherry-pick); `docs/Спецификация-2.0.md` — входное ТЗ (Universal
  Modular Appliance Platform); `docs/Архитектура-2.0.md` — аудит 1.1
  по слоям Hardware→Capabilities→Core→Modules→Roles, gaps с
  обоснованиями, черновики контрактов core API; `ROADMAP.md` — фазы
  2.0-1…2.0-8 (Capabilities 2.0, Core skeleton+инвентаризация
  subprocess/systemctl, Jobs, Module manifest v2+permissions+trust,
  Roles v2, Network Core с транзакциями, Storage Core); `README` —
  статус «в разработке», боевые системы остаются на 1.1.

### Исправлено
- **Гонка `wait()` в jobs:** событие выставлялось до persist в sqlite —
  `wait()` мог вернуться, пока строка в БД ещё `running` (read-after-wait
  видел устаревший статус); persist теперь идёт до `ev.set()`
  (`_run_one`, отмена queued-задачи); поймано ретраем
  `test_job_persisted_to_sqlite` под нагрузкой.

## [1.1.0] — 01.10.2026

### Добавлено
- **UI/UX-редизайн (STEP 1–12):** design system (`static/style.css`),
  доменные группы навигации, responsive-сетки, a11y (семантика,
  focus-visible, label), страницы dashboard/capabilities/roles.
- **Слой Hardware → Capabilities → Modules → Roles:**
  `core/capabilities.py` + `/api/capabilities`, модули v1
  (`module.json`: version/permissions/hardware, compute_status),
  роли (`PROFILES`, `apply_role`, `/roles`).
- **`GET /api/dashboard`** — агрегат системы/health/устройств/событий.
- **Скан через UI (P6):** выбор `network.scan_ifaces` чекбоксами на
  «Система → Настройки», тумблер `network.scan_enabled`, API
  `GET /api/network/ifaces`; `POST /api/settings` теперь deep-merge
  (частичный POST не стирает соседние ключи).
- **Identity устройств (P5):** MAC, уже известный на другом IP, — то же
  устройство: запись наследует `name`/`device_type`/`first_seen`
  (`is_new=0`), событие `IP_CHANGED` (info) с `metadata.old_ip`,
  прежний IP уходит в OFFLINE штатным механизмом misses.
- **Семверы/чейнджлог (P10):** `APP_VERSION=1.1.0` (было 0.9.0),
  `CHANGELOG.md` (Keep a Changelog), версия в `/api/health` и на
  «О системе», `version` в `meta.json` update.sh.
- Модульная справка (`/help` — контент из `help.md` модулей),
  гостевой доступ Samba (`core/samba_guest.py`).

### Изменено
- Статусы monitoring/семантика классов, устройства: список 6 колонок +
  детальная страница 3 уровнями.

### Безопасность
- **PHASE 16:** №61 `tools/sync_check.py` (дрейф-контроль repo↔сервер),
  №59 `network.scan_enabled` (одна ведущая копия, стоп двойного скана),
  №60 локализация CDN (vendor xterm/socket.io + CSP),
  №62 регресс-пентест (оба узла): `GET /api/settings` закрыт для guest
  (`admin_required`), restore-server выключен по умолчанию.

## [1.0.0] — 01.10.2026

Первый production-релиз (PHASE 1–15). Ключевое:

### Добавлено
- **Обновление/откат:** `update.sh` (бэкап → apply → verify → health,
  авто-rollback), `install.sh` (чистая установка), `recovery.sh`
  (восстановление из бэкапов, дрил PASS).
- **Бэкапы:** код+settings+БД с ротацией (`deploy/backup-db.sh`),
  restore через UI, эММС-бэкапы.
- **Discovery engine** (`core/discovery.py`): scanner → normalizer →
  reconcile → события v2 (`severity`/`source`/`metadata`), MAC_CHANGED.
- **Тесты и CI:** unit + live (pytest), CI на каждый push.
- **Документация:** комплект `docs/` (Архитектура, Безопасность, API,
  Установка, Обновление, Восстановление, Конфигурация, Пентест…).
- **Переносимость:** одна версия на X96 (aarch64) и Orange Pi (armv7l).
- **Безопасность:** Fernet-сейф паролей, session TTL + rate-limit
  логина, CSRF, systemd-хардening, threat model «root by design».

[1.1.0]: https://github.com/kotmartovskiy/Lan-discovery-1.1/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/kotmartovskiy/Lan-discovery-1.1/releases/tag/v1.0.0

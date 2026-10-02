# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/). Версия панели —
`APP_VERSION` в `app.py` (показывается в `/api/health`, `/api/system/health`
и на странице «О системе»). Git-тег `vX.Y.Z` ставится на релиз.

## [2.0.0] — не выпущена (в разработке)

### Добавлено
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

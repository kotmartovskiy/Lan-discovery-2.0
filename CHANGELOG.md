# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/). Версия панели —
`APP_VERSION` в `app.py` (показывается в `/api/health`, `/api/system/health`
и на странице «О системе»). Git-тег `vX.Y.Z` ставится на релиз.

## [2.0.0] — не выпущена (в разработке)

### Добавлено
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

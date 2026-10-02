# ROADMAP.md — LAN Discovery → production-quality portable appliance

**Дата аудита (PHASE 0):** 30.09.2026
**Статус аудита:** DONE (код не менялся)
**Область:** локальный репозиторий + обе платформы (X96 Max, Orange Pi Plus) по SSH (read-only).

---

## 0. Охват аудита

Проверено:
- структура репозитория, git-состояние, точка входа `app.py`;
- зависимости (`requirements.txt`, venv на платформах);
- systemd units и таймеры на обеих платформах;
- SQLite: схема, индексы, WAL/busy_timeout, миграции, retention, бэкапы;
- все 161 web-роут, декораторы авторизации, CSRF, SocketIO;
- вызовы `subprocess`/shell, path traversal, секреты, привязка адреса;
- hardcoded IP/интерфейсы/пути, platform-specific код;
- фоновые потоки и планировщики;
- синхронизация репозиторий ↔ серверы (помд5 всех файлов);
- тесты, CI, документация.

Не проверялось (требует отдельной работы): нагрузочные/конкурентные тесты, реальный
security pentest, восстановление из backup на чистую систему, поведение после reboot
(по логам — сервис auto-start есть, фактический reboot-тест не проводился).

---

## 1. Платформы (факт на 30.09.2026)

| | **X96 Max** | **Orange Pi Plus 2** |
|---|---|---|
| Hostname / IP | `armbian` / 192.168.3.243 | `orangepiplus` / 192.168.3.234 (LAN) |
| SoC / arch | Amlogic S905X2, **aarch64** | Allwinner H3, **armv7l** |
| OS | Armbian 26.11 **bookworm (Debian 12)**, kernel 6.18.51-ophub | Armbian 26.11 **trixie (Debian 13)**, kernel 6.18.46-sunxi |
| Питание/загрузка | **boot с SD-карты, временно без HDD — тестовый/резервный вариант** | основной носитель (eMMC + HDD, backup-emmc таймеры) |
| Код панели | **новая версия** (модульная: `core/`, `modules/` 111 файлов) | **старая версия** (`core/` отсутствует, `modules/` 22 файла) |
| `app.py` | md5 `ede31088…` == репозиторий | md5 `004fea92…` — другой (старый) |
| Service | enabled/active, NRestarts=0, RAM ~68 МБ | enabled/active, NRestarts=0, RAM ~43 МБ |
| RAM всего | 3309 МБ | 2006 МБ |
| Бэкап БД | `/srv/backup-db` — 3 файла (таймер 03:30, **timer ещё не срабатывал**) | `/srv/backup-db` — 182 файла (часовые, идёт давно) |
| Таймеры | weather-update, backup-db, update-iptv, env-data | + backup-emmc, backup-emmc-test |

**Вывод:** ~~сейчас в работе **две разные версии приложения** на двух платформах,
обе активно сканируют одну подсеть. Это главный источник дрейфа (см. §9).~~
**Обновлено 01.10.2026 (P4):** на обеих платформах — **одна и та же версия**
(repo == X96 == OP, md5 `app.py` `26ca64a1…`); X96 (LAN `192.168.3.243`) —
основная панель, Orange Pi — доступен через WiFi AP `192.168.3.235`
(LAN `.234` отвалился кабелем); обе активно сканируют подсеть (двойное
сканирование оставлено осознанно); юниты на venv + systemd-хардening P8.
Таблица выше — **факт аудита 30.09.2026** (до P4), сохранена как история.

---

## 2. Сводная таблица

| Area | Current state | Target | Gap | Priority |
|---|---|---|---|---|
| **Security: веб-терминал** | ~~SocketIO `terminal_start` **без авторизации** → PTY `/bin/bash` **от root**; `cors_allowed_origins="*"`~~ **закрыта (P0-1, 30.09)**: `_socket_is_admin()` (session → admin + enabled), `connect` отклоняет не-admin, события терминала проверяются, CORS-заглушка убрана (same-origin); residual: PTY root («root by design», задокументировано) | только admin, Origin ограничен |任何人 с доступом к порту получает root-shell | **P0** (закрыта) |
| **Security: filemanager** | ~~корень `/`, операции только под `@login_required` (гость может `shutil.rmtree`)~~ **закрыта (P0-2, 30.09)**: 7 роутов `/api/filemanager/*` → `@admin_required` + валидация путей (нормализация, запрет выхода за root) | admin-only + корень-ограничение | полное управление ФС от root | **P0** (закрыта) |
| **Security: неавторизованные роуты** | ~~`/api/network/check`, `/api/network/check_host` (POST, user host → команда), `/api/health` — без декораторов~~ **закрыта (P0-3, 30.09)**: `@login_required` + `_valid_host` для nettools-роутов, host из UI только в белом-списке; `/api/health` оставлен unauth осознанно (мониторинг) | авторизация (health — по решению) | DoS/SSRF-вектор без входа | **P0** (закрыта) |
| **Security: сейф паролей** | ~~«шифрование» = base64 + HMAC, который игнорируется при несовпадении; чтение доступно guest~~ **закрыта (P0-4, 30.09)**: Fernet-шифрование + миграция старых записей, чтение/запись через `can_edit` (guest закрыт), ключ в `secret.key` | настоящее шифрование (Fernet/AES-GCM) | пароли в открытом виде (+base64) | **P0** (закрыта) |
| **Security: авторизация** | ~~`enabled` не проверяется в `login_required`; SHA-256-фолбэк; нет session TTL; смена пароля без мин. длины~~ **закрыта (P0-5, 30.09)**: `enabled` + TTL сессии (12ч), min-8 символов, lazy re-hash SHA-256 → bcrypt при первом входе, rate-limit логина (5 попыток/300с на IP) | отключённый юзер = 401, только bcrypt, TTL сессии | отключённый пользователь остаётся в системе | **P0** (закрыта) |
| **Security: CSRF во вкладках** | ~~`base_app.html` **без csrf-meta** в репозитории → 11 шаблонов `/apps/*` POST без токена~~ **закрыта (P0-6, 30.09)**: csrf-meta + fetch-обёртки закоммичены из X96 в git (`base_app.html` + 11 шаблонов), проверено на обеих панелях | csrf-meta + fetch-wrapper в обеих базовых шаблонаках | POST-запросы из вкладок падают/незащищены | **P0** (закрыта) |
| **Security: заголовки/HOST** | ~~нет X-Frame-Options/CSP/X-Content-Type; `SESSION_COOKIE_*` не настроены~~ **закрыта (P1-10, 30.09)**: `_security_headers` (nosniff, X-Frame-Options SAMEORIGIN, Referrer-Policy same-origin), cookie `HttpOnly`+`SameSite=Lax` (без Secure — LAN HTTP); CSP добавлен позже (№60, 01.10 — vendor CDN локально); dev-Werkzeug `allow_unsafe_werkzeug=True` остаётся — осознанный threat model «root by design» (PHASE 8, docs/Безопасность.md) | заголовки, cookie-флаги, (опц.) reverse-proxy | clickjacking/доп. экспозиция | **P1** (закрыта) |
| **Security: секреты в git** | ~~`AGENTS.md`, `docs/*`, `templates/help.html` — root/1234, рабочие IP; скрипты с `password='1234'`; `sanitize_docs.py` чистит только `docs/`~~ **закрыта (P1-10, 30.09)**: help.html вычищен, 10 скриптов → env-переменные (`LAN_SSH_*`, `LAN_PANEL_PASS`), `make_demo.py` scrub + `demo_lint.py` (контент и имена файлов), публичный demo перегенерирован (leaks: 0); `sanitize_docs.py` дополнен P14 (wiki-regex, `admin/1234`); `AGENTS.md` с кредами — осознанно приватный ops-файл | чистка всех отслеживаемых файлов | утечка реквизитов в публичные репо (docs/demo) | **P1** (закрыта) |
| **Stability: изоляция сбоев** | ~~большинство роутов в `try/except`, но: DDL в `get_db()` на **каждый запрос**; утечки `con.close()` при исключениях (нет `finally`); падение импорта модуля валит всё приложение~~ **закрыта (P1-7, 30.09)**: схема БД — однократный init/ensure (user_version, индексы), `finally`-close коннектов, guard'ы импортов модулей; остаток: context manager не везде, физический io/SMART не опрашивается (осознанно) | схема один раз при старте; context manager; тест «SMART/Wi-Fi/nmap нет» | часы/диски/сеть отсутствуют → 500 в отдельных роутах (не фатально), но нет системного барьера | **P1** (закрыта) |
| **Stability: фоновые задачи** | ~~`start_scan_thread()` без guard от повторного запуска; `/inventory/scan` и bluetooth-scan без lock → параллельные прогоны; дубли функций (`app.py` ↔ `core_routes.py`), **мёртвый код** `init_background_tasks`~~ **закрыта (P1-8, 30.09)**: guard-флаги у scan/inventory/bt-задач, `init_background_tasks` и дубли core↔app удалены, единый источник в `core/` | lock/flag на каждую задачу, один источник истины | race: параллельные nmap/сканы, лишняя нагрузка | **P1** (закрыта) |
| **Stability: systemd** | `Restart=always`, `RestartSec=5`, `After=network-online` — корректно; ~~сервис работает от root и dev-сервер Werkzeug~~ **закрыта (P8, 30.09)**: systemd-хардening в юните (`PrivateTmp`, `ProtectKernel{Tunables,Modules,ControlGroups}`, `LockPersonality`, `RestrictRealtime`; verify + рестарт + смоук на X96), «root by design» + dev-Werkzeug зафиксированы threat model в `docs/Безопасность.md` (вариант «задокументированное root by design»); `ProtectHome`/`SystemCallFilter` осознанно не включены | non-root + (wsgi) либо задокументированное «root by design» | root-веб-сервер | **P1** (закрыта через P8) |
| **Configuration** | сеть/скан/интерфейсы/порт подключены к `/etc/lan-discovery/settings.json` через `_cfg` (P2: `subnet`, `scan_interval`/`max_misses`, `wifi_ifaces`, `traffic_ifaces`, `self_ips`, `web.flask_host`/`flask_port`; удалён мёртвый `app.NETWORK`); пути `/opt|/etc|/srv` остаются в коде (задокументированы в `docs/Конфигурация.md`) | единый конфиг: defaults + settings.json overrides | частично: пути захардкожены осознанно | **P2** (закрыта) |
| **Hardware abstraction** | `core/hardware.py`: `detect_platform()`/`thermal_temp()`/`hdd_device()`/`emmc_device()`/`sd_device()` — thermal-перебор зон и динамический детект дисков вместо хардкодов (`thermal_zone0`, `mmcblk2`, `/dev/sda`); `platform`-блок в `/api/health`; остаток: пути `/opt|/etc|/srv` (конфиг, PHASE 2) | `detect_platform()` + capabilities + адаптеры | закрыта (пути — в PHASE 2) | **P2** (закрыта) |
| **Переносимость (X96)** | ~~новый код уже работает на aarch64/Debian 12; старый — на armv7l/Debian 13~~ **закрыта (P4, 01.10)**: новая версия задеплоена и на Orange Pi (armv7l/Debian 13, venv `--system-site-packages` + apt cffi/cryptography/bcrypt — на armhf нет wheel, install.sh поправлен), md5==repo, unit 54/54, health/login — одна версия на обеих | один код на обеих | две версии в проде — **устранено** | **P2** (закрыта) |
| **Database** | ~~нет версионирования/индексов/retention, 8 таблиц без CREATE в репо~~ **закрыта (P5, 30.09)**: нумерованные `MIGRATIONS` по `user_version` (v1 базовая, v2 events), ensure 8 «серверных» таблиц (DDL из боевой БД) + индексы `events(ip,id)`/`events(event,id)`/`weather_observations`, retention `events.retention_days` (180д, чистка при старте + ежедневный поток); WAL + busy_timeout; **identity MAC+IP — закрыта (№63, 01.10)**: MAC, известный на другом IP, наследует name/device_type/first_seen, событие `IP_CHANGED` (metadata.old_ip) | `user_version` + миграции, индексы, retention, идентичность MAC+IP+hostname | hostname в identity не участвует (каноничность под вопросом) | **P5** (закрыта) |
| **Discovery engine** | ~~скан плотно связан с UI-роутами, сеть захардкожена, нет ручного выбора, MAC-смена не пишется в events~~ **закрыта (P6, 30.09)**: движок вынесен в `core/discovery.py` (scanner `run_scan` → normalizer `parse_scan` → `reconcile` → events), интерфейсы/подсеть из `network.scan_ifaces`/`subnet`, ручной `POST /api/scan` + кнопка в UI, событие `MAC_CHANGED`; **выбор интерфейсов через UI — закрыт (№63, 01.10)**: чекбоксы `scan_ifaces` + тумблер `scan_enabled` в «Система → Настройки», API `/api/network/ifaces` | scanner → normalizer → DB → events → API, manual/scheduled scan по выбору | ручной скан и scheduled есть; выбор интерфейса — через `scan_ifaces` (не через UI-форму) | **P6** (закрыта) |
| **Event system** | ~~только 3 типа, нет severity/source/metadata~~ **закрыта (P7, 30.09)**: events v2 — `severity`/`source`/`metadata` (JSON), фабрика `core/events.py` (`add_event`), `GET /api/events` с фильтрами, бейджи в `/history`; типы событий: NEW/ONLINE/OFFLINE/MAC_CHANGED | формализованные события (device_missing, ip_changed, disk_warning…) | severity/источник/фильтры есть; новые типы (disk_warning, ip_changed) подключаются через `add_event` одной строкой | **P7** (закрыта) |
| **Observability** | ~~`/api/health` есть (unauth), но **нет version/uptime/last-discovery в одном месте**~~ **закрыта (P1-9 / PHASE 12, 30.09)**: `/api/health` — version, uptime, last-discovery, db-status (+ capabilities/missing_deps, platform); `/api/status` и `/api/system/health` дополнены | `/api/health` + `/api/version` + `/api/discovery/status` | состояние видно только через UI | **P1** (закрыта) |
| **Installer** | ~~нет: установка вручную~~ **закрыта (P9, 30.09)**: `install.sh` в корне репо — preflight → code → apt-зависимости → venv → базовый settings (авто-subnet) → db init → systemd-юнит (`deploy/lan-discovery.service`, ExecStart под prefix) → health-retry; флаги `--prefix/--unit-dir/--skip-apt/--no-enable/--dry-run`, идемпотентен (проверено двойным прогоном на X96 в tmp-префиксе, состояние БД/config/юнита не изменилось); `docs/Установка.md` дополнен | `install.sh`: dep-check → config → systemd → db init → health | нестандартный `--prefix`: пути БД/settings захардкожены в коде; полный e2e «чистой машины» без Docker не воспроизведён (проверка — tmp-префикс + dry-run) | **P9** (закрыта) |
| **Update/rollback** | ~~деплой вручную, нет отката кода~~ **закрыта (P10, 30.09)**: `update.sh` — бэкап (tar кода + settings + sqlite-бэкап БД в `/var/backups/lan-discovery/<ts>` c meta.json/git-rev, ротация `--keep`) → apply (`--from`/`git pull`) → verify py_compile → pip → restart → health-retry; **авто-rollback** при сбое verify/health (восстановление кода/БД + restart + контрольный health), ручной `--rollback [TS]`, `--dry-run`; бэкап БД автоматический + restore из UI уже были | версия → backup → update → health → rollback | нет семантических версий/чейнджлога (трассировка — git-rev в meta.json); `--from` не удаляет исчезнувшие из новой версии файлы — **семверы/чейнджлог закрыты (№63, 01.10)**: `APP_VERSION=1.1.0` + `CHANGELOG.md` + `version` в meta.json | **P10** (закрыта) |
| **Backup/recovery** | ~~restore на чистую систему не проверен~~ **закрыта (P11, 30.09)**: `backup-db.sh` пишет и `config_*.tar.gz` (тар `/etc/lan-discovery`) рядом с `devices_*.db`; `recovery.sh` — авто-pick последних бэкапов (код `code.tar.gz` + конфиг + БД), verify (py_compile + sqlite integrity/user_version/counts), `--unit`/`--dry-run`/`--no-restart`; **дрил PASS** в изолированном `/tmp/lanrec` (config md5 == боевому, идемпотентность, боевые данные целы); `docs/Восстановление.md`; было: Online Backup API + integrity_check + ротация 14 дней + restore через UI, эММС-бэкапы на OP | документированная и проверенная процедура restore | «production ready только после проверенного restore» | **P11** (закрыта) |
| **Testing** | ~~pytest/CI нет; 3 ad-hoc скрипта требуют живой панели~~ **закрыта (P13, 30.09)**: `tests/` в репо — 42 unit (discovery/events/hardware/migrations/config, без сети) + 5 live (маркер `live`, скип при недоступности панели), `pytest.ini`+`requirements-dev.txt`, CI на каждый push/PR (ubuntu/py3.11: pytest unit + py_compile); ad-hoc `/tmp/test_p*`-скрипты остались как deploy-проверки фаз | unit + интеграционные в одном прогоне | живые ad-hoc-скрипты фаз не в git (deploy-only) | **P13** (закрыта) |
| **Documentation** | ~~docs/Архитектура.md устарел, комплекта нет~~ **закрыта (P14, 30.09)**: комплект 1.0 — 14 страниц: добавлены `docs/API.md` (163 роута: метод/путь/доступ/описание, из кода), `docs/Безопасность.md` (роли/CSRF/секреты/периметр/риски/чек-лист); `docs/Архитектура.md` освежён (14 таблиц БД вместо 11, core/hardware+discovery+events, MIGRATIONS по user_version, retention, lifecycle-скрипты, tests/CI); README/Home/_Sidebar — все страницы, install.sh в быстром старте, структура репо; линкер-проверка 74 ссылок = 0 битых; `sanitize_docs.py` fixed (wiki-regex для новых страниц + `admin/1234`) → leaks 0, exit 0; было: docs/ 9 страниц, README, AGENTS | комплект 1.0 | документация отстаёт от кода | **P14** (закрыта) |
| **Repo hygiene** | в корне 60+ одноразовых скриптов (`check_*`, `debug*`, `verify*` — большая часть в `.gitignore`); ~~трекаются `modules/*_b64.txt`, `patch_app.py`, `ssh_query.py`…; `.gitattributes` нет~~ **закрыта (01.10, d4535c5)**: удалены 13 junk-файлов из git (одноразовые скрипты, b64-заглушки, `ophub-issue-draft.md`) + 2 битые путь-копии (`templates__help.html`, `tools__make_demo.py`); `.gitattributes` добавлен 30.09 (P0-6); остаток — только `.gitignore`-мусор вне git | мусор вне корня/git | грязь в репозитории | **P4** (закрыта) |

---

## 3. Что уже работает хорошо (НЕ ломать)

1. **Модульная система** (`core/module_loader.py`, `core/module_catalog.py`): манифесты,
   вкладки/блоки/плитки, установка зависимостей, защита от path traversal в каталоге
   модулей, кэш 5 мин — зрелая и полезная конструкция.
2. **Бэкап БД** — эталон для проекта: `backup-db.sh` (Online Backup API →
   `PRAGMA integrity_check` → ротация), таймер, restore из UI с проверкой.
3. **CSRFProtect глобально** + fetch-обёртка в `base.html`; `@csrf.exempt` отсутствует.
4. **Авторизация**: bcrypt, роли admin/editor/guest, rate-limit логина (5/300с по IP).
5. **Каталог модулей**: валидация id, проверка путей в архиве, лимит файлов, токен из конфига.
6. **systemd**: `Restart=always`/`RestartSec=5`, таймеры с `Persistent=true`.
7. **SQLite**: WAL + `busy_timeout=30000` в горячих путях, все DDL `IF NOT EXISTS`.
8. **Service management**: allowlist имён/действий в `/api/service/<svc>/<action>`.
9. **Код в основном работает через argv-списки** (`shell=False`): `os.system`/`shell=True`
   буквально нет; единственный скрытый `shell=` — `app.py:82` (срабатывает только при
   строковом аргументе, сейчас все вызовы — списки).
10. Обе платформы: сервисы active, NRestarts=0, integrity БД `ok`.

---

## 4. Blockers (P0) — найденные проблемы

| # | Проблема | Место | Эффект |
|---|---|---|---|
| B1 | ~~Веб-терминал без авторизации, root-PTY, CORS `*`~~ **РЕШЁН (P0-1)** — SocketIO admin-only, CORS same-origin; root-PTY — **закрыт в PHASE 8 как «root by design»** (admin-only + сессии TTL + threat model в docs/Безопасность.md, задача 49); dev-Werkzeug — там же | `core_routes.py`, `app.py` | RCE закрыт для не-admin |
| B2 | ~~Filemanager: корень `/`, guest может удалять/перемещать файлы~~ **РЕШЁН (P0-2)** — 7 API → `@admin_required`, нормализация путей; корень `/` оставлен для admin | `core_routes.py` | потеря данных закрыта |
| B3 | ~~`POST /api/network/check_host` без авторизации (host → внешняя команда)~~ **РЕШЁН (P0-3)** — `@login_required` + `_valid_host` во всех nettools | `network_routes.py` | неаутентифицированный SSRF/DoS закрыт |
| B4 | ~~Сейф паролей: base64, HMAC игнорируется, guest читает~~ **РЕШЁН (30.09.2026, P0-4)** — Fernet + `@can_edit` | `core_routes.py` secrets-helpers | пароли открыты — закрыто |
| B5 | ~~Отключённый пользователь не выкидывается из сессии; SHA-256-фолбэк пароля~~ **РЕШЁН (30.09.2026, P0-5)** — enabled+TTL в `get_current_user`, lazy re-hash bcrypt, min 8 | `auth.py` | обход блокировки учётки — закрыт |
| B6 | ~~CSRF-meta отсутствует в `base_app.html` **в репозитории**~~ **РЕШЁН (30.09.2026, P0-6)** — правки X96 забраны в репо | `templates/base_app.html` | регресс CSRF при следующем деплое — закрыт |

Не P0, но рядом: ~~dev-Werkzeug на `0.0.0.0`, нет security-заголовков, root-сервис~~ — заголовки закрыты (P1-10), root/Werkzeug — задокументированный threat model + systemd-хардening (PHASE 8).

---

## 5. Синхронизация «репозиторий ↔ серверы»

**Статус: ВЫПОЛНЕНО (30.09.2026, P0-6) — repo == X96, content_diff=0.**

Итоговая сверка md5 (127 файлов): **exact=120, EOL-only=0, content diff=0,
только на сервере=0**. Что было сделано:
- забраны в репо правки X96: `templates/base_app.html` (+24 CSRF-fix, B6),
  `modules/recycling.py` (retry-логика vitaminstir), `templates/login.html` (favicon);
- задеплоены с X96: `modules/monitor.py` (ThreadPoolExecutor) и
  `templates/apps/terminal.html` (восстановлен из репо — на X96 была повреждена
  кодировка) → md5 байт-в-байт;
- 14 EOL-only файлов перезалиты (LF→LF), добавлен `.gitattributes` (`eol=lf`);
- 7 junk-файлов `templates/apps\*.html` (буквальный backslash, артефакт
  загрузки с Windows) перемещены в `/tmp/junk-templates-20260930-034105/`;
- только в git (ожидаемо, не код панели): `docs/`, `deploy/`, `AGENTS.md`,
  `ROADMAP.md`, разовые скрипты, `modules/*_b64.txt` (кандидат на чистку P4).
- OP (Orange Pi) — **другая, старая версия** (нет `modules/`), отдельная задача.

---

## 6. Первые задачи (P0/P1), к выполнению

1. **[P0][DONE — 30.09.2026]** Закрыть терминал: авторизация SocketIO-подключений
   (session/user + admin), ограничить `cors_allowed_origins` → см. журнал §9.
2. **[P0][DONE — 30.09.2026]** Filemanager: все 7 API-роутов → `@admin_required`,
   нормализация путей (`_fm_path`: строка/abs/null-байт), запрет `delete /` и
   `move /`, заглушка «только для admin» в шаблоне — см. журнал §9.
   Решение: корень `/` для admin оставлен (эквивалентен уже имеющемуся у admin
   root-терминалу; ограничение корня сломало бы назначение инструмента).
3. **[P0][DONE — 30.09.2026]** Закрыты `/api/network/check`, `/api/network/check_host`
   (`@login_required`); валидация `host` (`_valid_host`: имя/IPv4/IPv6, без пробелов,
   `/`, ведущего `-`) во всех nettools; валидация MAC (`_valid_mac`) в bluetooth —
   см. журнал §9.
4. **[P0][DONE — 30.09.2026]** Сейф: Fernet (authenticated encryption) на
   отдельном ключе `/etc/lan-discovery/secret.key` (chmod 600), миграция legacy
   base64+HMAC-записей (HMAC теперь реально проверяется), битые записи не
   двойношифруются, все 4 API → `@can_edit` — см. журнал §9.
5. **[P0][DONE — 30.09.2026]** Auth: `enabled` + TTL сессии (12ч) проверяются
   в `get_current_user` (сессия выкидывается), SHA-256 — только на первом входе
   с мгновенным re-hash в bcrypt, мин. длина пароля 8, дубли хелперов в
   `core_routes` заменены импортом из `modules.auth` — см. журнал §9.
6. **[P0][DONE — 30.09.2026]** Синхронизация: X96-правки (CSRF base_app,
   recycling, favicon) забраны в репо, `terminal.html` починен на X96,
   `monitor.py` задеплоен, `.gitattributes` добавлен, `apps\*.html` удалены —
   см. журнал §9.
7. **[P1][DONE — 30.09.2026]** Схема БД: DDL из `get_db()` → однократная
   инициализация (startup), `PRAGMA user_version`, индекс `events(ip, id)`,
   `finally`-закрытие коннектов — см. журнал §9.
8. **[P1][DONE — 30.09.2026]** Фоновые задачи: guard от повторного запуска
   сканов (scan/inventory/bluetooth), удалён мёртвый `init_background_tasks`
   и дубли функций (`app.py` ↔ `core_routes.py`) — см. журнал §9.
9. **[P1][DONE — 30.09.2026]** Observability: `/api/health` → version/
   uptime/last-discovery/db-status в один ответ — см. журнал §9.
10. **[P1][DONE — 30.09.2026]** Безопасность окружения: security-заголовки,
     cookie-флаги, чистка root/1234 и IP из отслеживаемых файлов (help.html,
     скрипты, демо-пайплайн) — см. журнал §9.
11. **[P1][DONE — 30.09.2026]** Обработка отсутствующих подсистем: probe
     capabilities (nmap/iw/smartctl/…) → `/api/health`, глобальный
     `FileNotFoundError` → дружелюбный 503 (JSON для API), честный отказ
     discovery без nmap (фикс фиктивного `SCAN OK` + интерфейсы `eth0/wlan0`)
     — см. журнал §9.

**PHASE 2 — Configuration (задачи P2):**

12. **[P2][DONE — 30.09.2026]** Хардкоды → `_cfg` (см. §2 строка «Configuration»):
     подключены уже существующие ключи `settings.json` — `network.subnet` в
     сканере/inventory, `scan_interval`/`max_misses` (вместо мёртвых констант
     app/devices/system), `wifi_ifaces` в iw-scan, `traffic_ifaces` в
     мониторинге трафика, self-check IP через `self_ips`,
     `web.flask_host`/`flask_port` в `socketio.run`, удалён мёртвый
     `app.NETWORK` — см. журнал §9.
13. **[P2][DONE — 30.09.2026]** Тест override-конфига: подменённый
     `settings.json` → ленивые хелперы читают новые значения
     (subnet/interval/self_ips), регрессии P1-9/P1-11, восстановление боевых
     настроек — 18/18 PASS, см. журнал §9.
14. **[P2][DONE — 30.09.2026]** Документация: `docs/Конфигурация.md` (все ключи
     `settings.json`, отдельный `network.json`, how-to «новая сеть/платформа»),
     прогон `sanitize_docs` (leaks 0), журнал §9 — см. журнал §9.

**PHASE 3 — Hardware abstraction (задачи P3):**

15. **[P3][DONE — 30.09.2026]** `core/hardware.py`: `thermal_zone_path()`
     (перебор зон, не только `zone0`), `thermal_temp()`, `hdd_device()`,
     `emmc_device()`, `sd_device()`, `board_model()`, `detect_platform()`;
     убраны хардкоды `thermal_zone0`/`mmcblk2`/`/dev/sda` в `system_routes`
     (status/health/disks/smart/io-ticks) и `monitor`; clone-watcher берёт
     устройство цели из cmdline `dd of=`; `platform`-блок в `/api/health` —
     см. журнал §9.
16. **[P3][DONE — 30.09.2026]** Тест PHASE 3 — 22/22 PASS (unit + API + live;
     на X96: eMMC=`mmcblk2`, SD=`mmcblk1`, hdd=None, thermal 48°C,
     smart → «диск не обнаружен»), см. журнал §9.

**PHASE 6 — Discovery engine (задачи P6):**

17. **[P6][DONE — 30.09.2026]** Выделен движок: новый `core/discovery.py` —
     `parse_scan` (normalizer), `run_scan` (scanner: один цикл по
     `network.scan_ifaces`, raw-вывод nmap **без двойного парсинга/синтеза**,
     FileNotFoundError → None), `reconcile(con, current, now)` (вся DB-логика:
     NEW/ONLINE/OFFLINE/misses + **MAC_CHANGED**), `scan_loop`, статус,
     `start_scan_thread`; `devices_routes.py` → реэкспорт + роуты; импорты
     `app.py`/`system_routes` не изменились — см. журнал §9.
18. **[P6][DONE — 30.09.2026]** Manual scan: `POST /api/scan`
     (`{subnet?, ifaces?}` one-shot, admin-only, не пишет settings) →
     run_scan + reconcile, JSON `{ok, devices, stats, subnet}`; кнопка
     «🔍 Сканировать» в `templates/devices.html` (только admin;
     fetch-wrapper base.html сам ставит X-CSRFToken).
19. **[P6][DONE — 30.09.2026]** Трассируемость: смена MAC → событие
     `MAC_CHANGED` в `events` (name/device_type по-прежнему обнуляются);
     видно в `/history` и на странице устройства.
20. **[P6][DONE — 30.09.2026]** Тест PHASE 6 — 30/30 PASS (unit parse_scan/
     reconcile на временной БД: NEW → MAC_CHANGED → misses → OFFLINE → ONLINE,
     живой run_scan 22 хоста, POST /api/scan 403/400/ok, кнопка в devices,
     регрессии health/platform), см. журнал §9.

**PHASE 7 — Event engine (задачи P7):**

21. **[P7][DONE — 30.09.2026]** Схема events v2: `SCHEMA_VERSION = 2` —
     колонки `severity`/`source`/`metadata` (JSON), backfill старых строк по
     типу события, индекс `idx_events_event(event)`; боевые 37 119 событий
     сохранены, `integrity_check` ok — см. журнал §9.
22. **[P7][DONE — 30.09.2026]** Фабрика `core/events.py`: `EVENT_SEVERITY`,
     `add_event` (единая точка INSERT, metadata → JSON), `list_events`,
     `event_to_dict`; `core/discovery.py` пишет NEW/ONLINE/OFFLINE/
     MAC_CHANGED через фабрику (severity: info/warning, source: discovery).
23. **[P7][DONE — 30.09.2026]** `GET /api/events` (login; `limit`/`event`/
     `severity`/`ip`/`source`, JSON с парсингом metadata) + колонка «Уровень»
     (цветовой бейдж) в `/history`.
24. **[P7][DONE — 30.09.2026]** Тест PHASE 7 — 30/30 PASS (unit фабрики и
     reconcile, миграция v2 на боевой БД, API-фильтры, UI, регрессии P6/P3,
     live health `user_version=2`), см. журнал §9.

**PHASE 5 — Database (задачи P5):**

25. **[P5][DONE — 30.09.2026]** Формальная система миграций:
     `MIGRATIONS = ((1, _m1), (2, _m2))` в `init_db_schema` — строго по
     `PRAGMA user_version`, каждый шаг своя функция с логом; v1 = базовая
     схема devices/events, v2 = severity/source/metadata + `idx_events_event`
     + backfill; на чистой БД путь 0→1→2 исполняется пошагово — см. §9.
26. **[P5][DONE — 30.09.2026]** Ensure-таблиц: `CREATE TABLE IF NOT EXISTS`
     для 8 таблиц, у которых CREATE жил только на сервере (env_data,
     mchs_alerts, weather_alerts/daily/forecast/forecast_history/hourly/
     observations — точный DDL из боевой БД) + индекс
     `idx_weather_observations_timestamp_unique` — восстановление на чистой
     системе даёт полную схему — см. §9.
27. **[P5][DONE — 30.09.2026]** Retention событий: settings
     `events.retention_days` (default 180, 0 = выкл),
     `core/events.cleanup_old_events()` (парсинг DD.MM.YYYY, DELETE
     батчами) — чистка при старте + ежедневный daemon-поток
     `retention_loop` из `app.py __main__` — см. §9.
28. **[P5][DONE — 30.09.2026]** Тест PHASE 5 — 24/24 PASS (пошаговый путь
     v1→v2 на чистой БД с логами `DB MIGRATION`, ensure 8 таблиц, retention
     unit, боевая БД integrity ok, health/user_version=2, регрессии,
     live) — см. журнал §9.
29. **[P13][DONE — 30.09.2026]** pytest-структура в репо: `tests/unit`
     (не требуют сети/панели) + `tests/live` (маркер `live`, скипаются,
     если панель недоступна), `tests/conftest.py` (фикстуры events_con /
     devices_db / no_dns), `pytest.ini` (по умолчанию `-m "not live"`),
     `requirements-dev.txt` (pytest) — см. §9.
30. **[P13][DONE — 30.09.2026]** Набор тестов: **42 unit** — discovery
     (parse_scan, run_scan с моками subprocess, reconcile NEW/ONLINE/
     misses/OFFLINE/MAC_CHANGED, get_scan_status, guard scan-потока),
     events (severity-карта, фильтры, JSON metadata, retention unit),
     hardware (переносимые проверки Win/CI/X96), миграции (шаги v1/v2,
     идемпотентность, полный init, ensure-таблицы, retention_days),
     config (`_cfg`/`_scan_interval`/`_max_misses` через monkeypatch
     load_settings); **5 live** — health-форма, anon-редирект,
     login+`/api/events`, страницы, `/api/currencies`. Прогнано локально
     (Win/py3.12/pytest 8) и на X96 (venv/pytest 9) — см. §9.
31. **[P13][DONE — 30.09.2026]** CI: `.github/workflows/ci.yml` — на
     push/PR в main: ubuntu + Python 3.11 + `pip install -r
     requirements.txt -r requirements-dev.txt` → `pytest tests/unit -v` +
     py_compile core/app/modules; timeout 15 мин, cache pip — см. §9.
32. **[P9][DONE — 30.09.2026]** `install.sh` (корень репо) + шаблон
     `deploy/lan-discovery.service`: preflight (root/python3/код) →
     apt-зависимости (nmap/traceroute/dnsutils/iw/bluez/smartmontools/
     ffmpeg/mpv/python3-venv, `--skip-apt` для тестов) → venv + pip →
     базовый `settings.json` (авто-subnet/self_ips из интерфейсов, если
     файла нет) → db init → установка юнита (ExecStart под `--prefix`,
     enable --now при доступном systemd) → health-check с retry.
     Флаги: `--prefix`, `--unit-dir`, `--skip-apt`, `--dry-run`,
     `--no-enable`; каждый шаг идемпотентен — см. §9.
33. **[P9][DONE — 30.09.2026]** Проверка на «чистом» окружении: прогон
     install.sh на X96 в tmp-префиксе (`--prefix /tmp/... --unit-dir
     --skip-apt`): первый запуск создаёт venv/config/БД/юнит и проходит
     health; второй запуск идемпотентен (данные не тронуты, md5
     settings.json/devices.db совпадают, повторные шаги — no-op);
     `--dry-run` печатает план без изменений — см. §9.
34. **[P9][DONE — 30.09.2026]** `docs/Установка.md`: чистая установка на
     Debian/Armbian (3 способа получить код), описание каждого шага
     install.sh и флагов, первый вход (admin/1234 → смена пароля),
     проверка (systemctl/curl health), удаление — см. §9.
35. **[P10][DONE — 30.09.2026]** `update.sh` (корень репо): бэкап
     (tar кода + settings.json + sqlite-бэкап devices.db →
     `/var/backups/lan-discovery/<ts>/` с meta.json, ротация `--keep`,
     default 5) → apply (`--from DIR` копированием или `git pull
     --ff-only` если есть `.git`) → verify (`py_compile` app.py+core+
     modules) → pip -r requirements → `systemctl restart` → health-retry;
     **авто-rollback** при ошибке verify/health (распаковка бэкапа +
     restore БД + restart + контрольный health); ручной
     `--rollback [TS]`, `--dry-run` — см. §9.
36. **[P10][DONE — 30.09.2026]** Тесты update.sh на X96: dry-run (план);
     success-путь (`--from` с копией боевых файлов: rc=0, бэкап создан,
     health ok, код не повреждён); **fail-путь** (битый `app.py` →
     py_compile → авто-откат до бэкапа: md5 исходного app.py
     восстановлен, сервис жив, rc≠0); `--rollback` вручную (rc=0,
     health ok); ротация `--keep 2` — см. §9.
37. **[P10][DONE — 30.09.2026]** `docs/Обновление.md`: два способа
     (update.sh / git pull), таблица флагов, что делает авто-откат,
     где лежат бэкапы и ротация, ручной откат, связь с install.sh —
     см. §9.
38. **[P11][DONE — 30.09.2026]** Бэкап конфига: `deploy/backup-db.sh`
     дополнительно кладёт `config_<ts>.tar.gz` (тар `/etc/lan-discovery`:
     settings/users/secret.key/modules.json/notes/secrets/) рядом с
     БД-бэкапом, `tar tzf`-проверка целостности, ротация 14 дней; UI-список
     (`DB_BACKUP_PATTERN=devices_*.db`) tar не подхватывает — юнит-тест
     фильтра — см. §9.
39. **[P11][DONE — 30.09.2026]** `recovery.sh`: восстановление из
     бэкапов — код (`--code-tar`, default последний
     `/var/backups/lan-discovery/*/code.tar.gz`) + конфиг
     (`--config-tar`, default последний `/srv/backup-db/config_*.tar.gz`,
     в `--config-dir`) + БД (`--db`, default последний
     `/srv/backup-db/devices_*.db`, в префикс) → verify (py_compile
     системным python3 + sqlite integrity/user_version/counts) →
     опционально юнит (`--unit`) и restart+health (`--no-restart` для
     дрила); `--dry-run` — см. §9.
40. **[P11][DONE — 30.09.2026]** Дрил восстановления на X96: свежий
     прогон backup-db.sh (БД+конфиг-тар, состав полный, integrity);
     `recovery.sh` в изолированный `/tmp/lanrec` (код+конфиг+БД,
     integrity ok, user_version=2, counts>0, settings md5 == боевому);
     повторный прогон идемпотентен; боевые config/БД в дриле не тронуты —
     см. §9.
41. **[P11][DONE — 30.09.2026]** `docs/Восстановление.md`:
     документированная **проверенная** процедура восстановления на чистую
     систему (что где лежит, recovery.sh по шагам, ручные альтернативы,
     проверка после восстановления) — закрывает §2 «production ready
     только после проверенного restore» — см. §9.
42. **[P14][DONE — 30.09.2026]** `docs/API.md` — полный каталог роутов
     (163 шт., собран из `app.py` + `modules/*.py`): метод/путь/группа/
     доступ/описание, группы по модулям, отдельно JSON API vs страницы;
     перелинковка из «Модули»/README — см. §9.
43. **[P14][DONE — 30.09.2026]** `docs/Безопасность.md` — модель доступа
     (роли/декораторы/CSRF/сессии), секреты (`users.json`, `secret.key`),
     сделанный hardening (P0/P1-10), периметр (0.0.0.0, dev-сервер, нет
     TLS), бэкапы/restore, гигиена документации (sanitize), известные
     ограничения — см. §9.
44. **[P14][DONE — 30.09.2026]** `docs/Архитектура.md` приведён в
     соответствие с кодом: 14 таблиц БД (было 11) + `user_version=2`/
     MIGRATIONS, `core/hardware`/`core/discovery`/`core/events`,
     retention, lifecycle-скрипты (install/update/recovery/backup),
     tests/CI, структура deploy — см. §9.
45. **[P14][DONE — 30.09.2026]** README + Home + _Sidebar: таблица всех
     страниц docs (добавлены Обновление/Восстановление/Конфигурация/
     API/Безопасность), быстрый старт через `install.sh`, актуальная
     структура репо (core/, tests/, скрипты), навигация wiki — см. §9.
46. **[P14][DONE — 30.09.2026]** Сверка комплекта 1.0: линкер-проверка
     внутренних ссылок docs (битых нет), §2 Documentation закрыта,
     `python tools/sanitize_docs.py` без утечек, CI green, sync — см. §9.
47. **[P8][DONE — 30.09.2026]** `tests/unit/test_security.py` —
     security-регресс в CI через Flask `test_client`: заголовки
     (nosniff/XFO/Referrer-Policy), cookie-флаги (HttpOnly+SameSite=Lax,
     без Secure), аноним `/` → 302, анонимный POST без CSRF → 400 —
     см. §9.
48. **[P8][DONE — 30.09.2026]** `tests/live/test_security.py` — перенос
     ад-hoc `/tmp/test_p10_env.py` (27 чеков P1-10) в live-набор:
     заголовки на 5 точках, Set-Cookie, чистый `/help`, no-store на API,
     регрессии авторизации — см. §9.
49. **[P8][DONE — 30.09.2026]** systemd-хардening юнита
     (`deploy/lan-discovery.service`): `PrivateTmp`, `ProtectHome`,
     `ProtectKernel{Tunables,Modules,ControlGroups}`, `LockPersonality`,
     `RestrictRealtime` + проверка на X96 (restart → health → смоук);
     «root by design» + dev-Werkzeug зафиксированы как осознанный threat
     model в `docs/Безопасность.md` (вариант §2 «задокументированное
     root by design») — см. §9.
50. **[P8][DONE — 30.09.2026]** Итог PHASE 8: §2 (заголовки/куки и
     секреты в git закрыты P1-10, systemd/root — P8), §4 B1 — финальная
     отметка, регресс unit+CI+live, §7 → DONE, §9 — см. журнал.
51. **[P4][DONE — 01.10.2026]** Предусловия деплоя на Orange Pi
     (192.168.3.235/234): бэкапы `*.backup-pre-p4-*` в
     `/root/p4-backups/` (код 4.2MB без venv/backups/БД, юнит,
     `/etc/lan-discovery/`, sqlite backup `devices.db` 5.6MB), сервис
     остановлен, недостающие apt-пакеты (`traceroute`, `dnsutils`);
     старая схема `user_version=0`, 15 таблиц — миграции аддитивные.
52. **[P4][DONE — 01.10.2026]** Деплой: `git archive` HEAD → OP
     поверх `/opt/lan-discovery` (md5 `app.py` == repo); **открытие:
     на armhf у cffi/bcrypt/cryptography нет wheel**, pip падал на
     сборке cffi (`arm-linux-gnueabihf-gcc`) — решение: системные
     `python3-{cffi,cryptography,bcrypt}` из Debian 13 +
     `venv --system-site-packages` (остальное — pure pip);
     `install.sh --skip-apt` → юнит по шаблону (venv+хардening),
     `init_db_schema()` → миграции v0→v2, health OK.
53. **[P4][DONE — 01.10.2026]** Верификация на OP: md5 `app.py` == repo,
     **unit 54/54 PASS** (Python 3.13/armv7l), смоук login 302 /
     status 200 / health 200 / filemanager 200 (вход на 127.0.0.1 и
     192.168.3.235; `.234` — известный отвал LAN-кабеля, не регресс),
     `journalctl` чист, данные целы (settings/secret.key не тронуты;
     admin-hеш → bcrypt — штатный lazy rehash P0-5), доставлен
     `iputils-tracepath` (health: tracepath true).
54. **[P4][DONE — 01.10.2026]** Итог PHASE 4: §2 «Переносимость» —
     закрыта (одна версия на обеих), install.sh починен для armhf
     (apt cffi/cryptography/bcrypt + `--system-site-packages`),
     решение оператора: **обе панели сканируют LAN** (двойное
     сканирование оставлено, `scan_interval` не менялся), §7 → DONE,
     §9 — см. журнал.
55. **[P15][DONE — 01.10.2026]** Reboot-тест X96: до — enabled/
     active, NRestarts=0, health 200, db v2/15/40/37270; `systemctl
     reboot` → панель сама вернулась за ~50 с (boot 34.6s); после —
     enabled/active, NRestarts=0, health 200, **данные идентичны**
     (v2/15/40/37270), ошибок в journal за boot 0.
56. **[P15][DONE — 01.10.2026]** Disaster-recovery дрил на чистый
     префикс `/tmp/recovery-test` (X96): `install.sh --prefix …
     --unit-dir … --skip-apt --no-enable` — код (md5 == repo), venv
     `--system-site-packages`, юнит `ExecStart=/tmp/recovery-test/venv/…`;
     `recovery.sh` из свежих бэкапов (`devices_20260930-233341.db`,
     `config_*.tar.gz`, `code.tar.gz` из `/var/backups`) → SYNTAX OK
     (22 файла), **restored devices=40 events=37184 == бэкапу**,
     integrity=ok, config 16 элементов; уборка, боевой сервис не
     пострадал (active).
57. **[P15][DONE — 01.10.2026]** Нагрузочный smoke (live, X96):
     200×GET /api/status в 20 потоков, 100×status **во время**
     ручного scan, 10 параллельных логинов, 50 анонимных `/` —
     итог **356×200 + 9×302, 0×5xx/ERR, errors none** (дедлоков
     нет); scan под нагрузкой 200 за 31.7с; latency p50=3.4s
     p95=5.1s — dev-Werkzeug под 20-поточной нагрузкой (известное
     ограничение, гunicorn отложен threat model P8).
58. **[P15][DONE — 01.10.2026]** Итог Production 1.0: git tag
     `v1.0.0`, актуализация §1 (одна версия на обеих платформах),
     §7 → DONE, §9 — см. журнал.

---

## 7. Статусы фаз roadmap

| Фаза | Статус | Комментарий |
|---|---|---|
| PHASE 0 Audit | **DONE** | этот документ; код не менялся |
| PHASE 1 Stabilization | **DONE** | задачи 7–11 done |
| PHASE 2 Configuration | **DONE** | задачи 12–14: хардкоды → `_cfg`, тест override, docs/Конфигурация.md |
| PHASE 3 Hardware abstraction | **DONE** | задачи 15–16: `core/hardware.py` (detect_platform/thermal/storage), убраны хардкоды thermal_zone0/mmcblk2/sda, platform в health |
| PHASE 4 X96 Max port | **DONE** | задачи 51–54: новая версия задеплоена на Orange Pi (armv7l/Debian 13): md5==repo, unit 54/54, health/login OK, миграции v0→v2; install.sh починен для armhf; §2 «Переносимость» закрыта — одна версия на обеих |
| PHASE 5 Database | **DONE** | задачи 25–28: MIGRATIONS-карта по user_version, ensure 8 «серверных» таблиц, retention events (180д), тест 24/24 |
| PHASE 6 Discovery engine | **DONE** | задачи 17–20: `core/discovery.py` (scanner/normalizer/reconcile), `POST /api/scan` + кнопка, событие MAC_CHANGED |
| PHASE 7 Event engine | **DONE** | задачи 21–24: events v2 (severity/source/metadata, SCHEMA_VERSION=2), `core/events.py`, `GET /api/events`, бейджи в /history |
| PHASE 8 Security hardening | **DONE** | задачи 1–6 (P0) + 10 (P1-10) + 47–50 (P8): security-тесты в репо (8 unit в CI + 10 live), systemd-хардening юнита (проверено на X96: restart/health/scan/filemanager), «root by design» threat model в docs/Безопасность.md |
| PHASE 9 Installer | **DONE** | задачи 32–34: install.sh (8 шагов, идемпотент, dry-run), deploy/lan-discovery.service, docs/Установка.md; проверка на X96 в tmp-префиксе + повторный прогон без изменений данных |
| PHASE 10 Update/rollback | **DONE** | задачи 35–37: update.sh (backup → apply → verify → pip → restart → health с авто-rollback), docs/Обновление.md; тесты на X96: success/fail+авто-откат (md5 app.py восстановлен, сервис жив)/ручной rollback/ротация |
| PHASE 11 Backup/Recovery | **DONE** | задачи 38–41: backup-db.sh (config_*.tar.gz), recovery.sh (код+конфиг+БД+verify+юнит), дрил на X96 в изолированном префиксе (PASS, боевые данные не тронуты, идемпотентность), docs/Восстановление.md, юнит-тест фильтра UI-списка |
| PHASE 12 Observability | **DONE** | P1-9: `/api/health` + version/uptime/last-discovery/db-status |
| PHASE 13 Testing | **DONE** | задачи 29–31: pytest-структура (unit 42 / live 5, маркер `live`), CI GitHub Actions (ubuntu/py3.11: pytest unit + py_compile) на каждый push/PR |
| PHASE 14 Documentation | **DONE** | задачи 42–46: docs/API.md (163 роута, колонка доступа), docs/Безопасность.md, docs/Архитектура.md освежён (14 таблиц, core/, lifecycle, CI), README/Home/_Sidebar со всеми страницами, линкер 74/0, sanitize leaks=0, публичный репо docs обновлён |
| PHASE 15 Production 1.0 | **DONE** | задачи 55–58: reboot-тест X96 PASS (автостарт/данные), disaster-recovery дрил на чистый префикс PASS (==бэкапу), нагрузочный smoke PASS (0×5xx), git tag `v1.0.0` |
| PHASE 16 After 1.0 | **DONE (01.10.2026)** | задачи 59–68 (состав расширен 01.10 — 66–68), см. ниже; репо-hygiene и чистка §2 выполнены вне фазы 01.10; релиз `v1.1.0` |

### Состав PHASE 16 (задачи 59–68, определён/расширен 01.10.2026)

| # | Задача | Статус |
|---|---|---|
| 59 | **Двойное сканирование (§8.1):** одна ведущая копия — флаг `network.scan_enabled`/leader в settings либо стоп `lan-discovery`-скана на OP; без двойной нагрузки на сеть | **DONE (01.10.2026)** |
| 60 | **Локализация CDN (§8.5):** vendor-копии xterm.js/socket.io в `static/vendor/` + CSP; терминал работает без интернета | **DONE (01.10.2026)** |
| 61 | **Дрейф-контроль (§8.2):** `tools/sync_check.py` в репо (filelist+md5-сверка repo↔сервер), запуск по требованию/cron, отчёт о расхождениях | **DONE (01.10.2026)** |
| 62 | **Регресс-пентест по чек-листу** `docs/Пентест.md` на обоих узлах (чек-лист создан 01.10 отдельно от фазы) | **DONE (01.10.2026)** |
| 63 | **Gap'ы §2:** выбор интерфейсов скана через UI (P6), identity устройств MAC+IP (P5), семантические версии/чейнджлог поверх git-rev (P10) | **DONE (01.10.2026)** |
| 64 | **Харддинг-резидуалы:** решение по reverse-proxy/TLS либо подтверждение «root by design» на очередной квартал; обсуждение non-root | **DONE (01.10.2026)** |
| 65 | **§8 residual'ы:** проверить, что UI-блоки eMMC/clone/hdd корректно прячутся на X96 (boot с SD, без HDD) по всей панели, не только модулями | **DONE (01.10.2026)** |
| 66 | **Residual-фиксы по находкам №62/§8:** SSRF-валидация URL IPTV (только http/https) + маскировка `Server`-заголовка (без версий Werkzeug/Python) | **DONE (01.10.2026)** |
| 67 | **Автозапуск дрейф-контроля (§8.2):** ежедневная Windows-задача `LanDiscovery-SyncCheck` (`tools/setup_sync_task.ps1` + `tools/sync_check_daily.cmd`, лог в `%LOCALAPPDATA%\lan-discovery\`), пароль только локально вне репо | **DONE (01.10.2026)** |
| 68 | **Демо-слепок:** `make_demo.py` → `demo_lint.py` → push `Lan-discovery-demo` под актуальный UI (STEP 12, №59–67) | **DONE (01.10.2026)** |

---

## 8. Остаточные риски

1. ~~**Двойное сканирование**~~ — **закрыто (№59, 01.10)**: лидер X96,
   на OP `network.scan_enabled: false`, фоновый опрос только одной
   копией (проверено live: X96 `scan` растёт, OP `null`).
2. ~~**Дрейф кода:** правки на серверах без коммита (и наоборот) уже случались (§5);
   текущий регламент (ручной) не гарантирует синхронизацию.~~
   **Закрыто (№61 + №67, 01.10): `tools/sync_check.py` — сверка
   repo ↔ X96; ежедневная Windows-задача `LanDiscovery-SyncCheck`
   (09:30, `tools/setup_sync_task.ps1`, лог
   `%LOCALAPPDATA%\lan-discovery\sync_check.log`) + по требованию;
   дрейф документации ловится отдельно (`sanitize_docs`/push).**
3. **Werkzeug dev-сервер под root на 0.0.0.0** — терпимо только в закрытой LAN до P1;
   связанные находки №62: заголовок `Server: Werkzeug/3.x Python/3.x`
   отдаёт точные версии ПО — **закрыто (№66, 01.10): `Server:
   lan-discovery` (единственный, без версий; проверено live на X96)**.
   **Подтверждено «root by design» на
   квартал до 01.01.2027 (№64, 01.10)**; TLS/reverse-proxy и non-root
   отложены окончательно — триггеры пересмотра (удалённый доступ,
   WAN-проброс, второй не-админ) зафиксированы в
   `docs/Безопасность.md` → «Модель угроз».
4. **X96 без HDD, boot с SD** — тестовый режим: эММС/диск-функции (clone/backup-emmc)
   на нём неприменимы; **проверено по всей панели (№65, 01.10)**:
   кнопки eMMC-бэкапа disabled с причиной (+guard и на
   `backup-test`), строка HDD в блоке «Плата» и элементы HDD в
   status-bar шапки не рендерятся без данных, clone-btn disabled,
   capabilities отдаёт «— не обнаружено», справка — по факту хоста.
5. ~~**Внешние CDN** (xterm.js, socket.io)~~ — **закрыто (№60, 01.10)**:
   vendor-копии в `static/vendor/`, CSP без внешних CDN, терминал
   работает без интернета.
6. **Legacy SHA-256 у `user`/`guest` в users.json** (находка №62):
   хеши перейдут в bcrypt лениво при первом входе (P0-5); пароли этих
   юзеров неизвестны инструменту — вручную не перехешированы.
7. **WAN-проброс не проверяется** (находка №62): X96 без firewall
   (`NO_UFW`), панели слушают `0.0.0.0:8080`; отсутствие проброса
   8080/8081/9091 в WAN зависит от роутера и не подтверждалось извне
   (нет WAN-доступа); OP — ufw default deny incoming.
8. ~~**SSRF в IPTV** (находка №62): `POST /system/iptv/add` принимает URL
   без валидации схемы (`file://`, localhost, внутренние адреса —
   принимаются, плейлист скачивается сервером) — принято по LAN-модели.~~
   **Закрыто (№66, 01.10): принимаются только `http`/`https` с netloc
   (file://, gopher://, ftp://, javascript: — редирект без сохранения,
   юнит + живой прогон); localhost/внутренние адреса по http
   допустимы — LAN-модель.**

---

## 9. Журнал выполнения

### 30.09.2026 — P0-1: авторизация веб-терминала и SocketIO CORS — **DONE**

**Проблема (B1):** `terminal_start` без авторизации → root-PTY `/bin/bash`;
`cors_allowed_origins="*"` → любой origin мог держать socketio-соединение.

**Изменения:**
- `app.py` — `SocketIO(app, async_mode="threading")`: убран `cors_allowed_origins="*"`
  (default python-socketio = same-origin only, проверено по source venv);
- `modules/core_routes.py` — `_socket_is_admin()` (session → admin + enabled),
  `connect` возвращает `False` для не-admin; проверка на каждом terminal-событии
  (`terminal_start/input/resize`) с принудительным закрытием сессии; вычистка
  `terminal_stop`/`disconnect` через общий `_terminal_drop()`.

**Файлы:** `app.py`, `modules/core_routes.py` (repo == X96 после деплоя).

**Деплой:** бэкапы `app.py.backup-20260930-032130`, `core_routes.py.backup-20260930-032130`
→ pscp → `py_compile` OK → `systemctl restart lan-discovery` → active.

**Тесты (X96, `/tmp/test_terminal_auth.py`, 9/9 PASS):**
анонимный socketio-connect отклонён; admin login 302 + `GET /` 200;
admin socket connect + реальный вывод терминала (MOTD); после disconnect —
0 процессов `bash --login`; smoke: `/api/health` 200, `/login` 200, `/` 302 (неавториз.).

**Остаточные риски:** PTY по-прежнему от root (правка прав — отдельная задача);
`/apps/terminal` страница доступна не-admin (шаблон показывает заглушку) — ок;
socketio-события не покрываются CSRF (смягчено same-origin + session-auth);
Cross-Site WebSocket Hijacking закрыт Origin-проверкой python-socketio.

**Наблюдение (не блокер):** в первом bash-сеансе терминала появляется
«Waiting for system to finish booting…» и «Create root password:» —
армбиевский profile-sкрипт на медленном первом выводе; не связано с этим
изменением (логика `terminal_start` не менялась), вынести в отдельную проверку.

### 30.09.2026 — P0-2: filemanager admin-only + валидация путей — **DONE**

**Проблема (B2):** корень `/`, `shutil.rmtree`/`os.remove`/`shutil.move` под
только `@login_required` — guest мог изменять файловую систему от root.

**Изменения:**
- `modules/core_routes.py` — 7 роутов `/api/filemanager/*` → `@admin_required`;
  хелпер `_fm_path()` (строка, без null-байт, абсолютный после `normpath`) → 400;
  явный запрет `delete /` и `move /`;
- `templates/apps/filemanager.html` — `{% if current_user.role != 'admin' %}`
  заглушка (по образцу terminal).

**Файлы:** `modules/core_routes.py`, `templates/apps/filemanager.html`
(repo == X96 после деплоя). Бэкапы `*.backup-20260930-032856`.

**Тесты (X96, `/tmp/test_filemanager_auth.py`, 16/16 PASS):**
аноним: list→403, POST без CSRF→400 (CSRFProtect), POST с валидным CSRF→403;
admin: list→200, страница с UI; не-admin (`user`): list/read→403, страница→заглушка;
валидация: относительный путь→400, null-байт→400; регресс: health/login/apps.
Временный пароль `user` тест-account: users.json → бэкап → тест → **восстановлен**.

**Остаточные риски:** guest/editor больше не имеют доступа к filemanager
(изменение поведения — задокументировать); `/api/player/*` (browse/playlist без
нормализации путей) — отдельная задача hardening (см. P1-10).
(B6 про base_app CSRF — решён в P0-6.)

### 30.09.2026 — P0-3: авторизация network-роутов + валидация host/MAC — **DONE**

**Проблема (B3):** `/api/network/check` и `/api/network/check_host` (POST, host из
JSON → внешняя команда) были без авторизации; nettools передавали `host` в argv
без проверки (argument injection через ведущий `-`); bluetooth MAC уходил в stdin
`bluetoothctl` без проверки (инъекция команд).

**Изменения (только `modules/network_routes.py`):**
- оба `/api/network/check*` → `@login_required`;
- `_valid_host()`: `[A-Za-z0-9][A-Za-z0-9._:-]{0,252}`, без `/`, без ведущего `-`,
  без пробелов → иначе 400; применён к `check_host`, `ping`, `dns`, `ports`, `trace`;
- `_valid_mac()`: строгий формат `XX:XX:XX:XX:XX:XX` → иначе 400; применён к
  `connect`, `disconnect`, `pair`, `remove`.

**Файл:** `modules/network_routes.py` (repo == X96 после деплоя).
Бэкап `network_routes.py.backup-20260930-033214`.

**Тесты (X96, `/tmp/test_p03_network.py`, 29/29 PASS):**
аноним: GET check→302, POST no-csrf→400, POST с CSRF→302;
admin: check→200, check_host(127.0.0.1)→200 online=true;
инъекции: `-c`, `--help`, `a b`, `path`, tab, пустые → 400 (все 4 nettools тоже);
валидные host → работают (ping реальный вывод); bt-инъекция
`AA:BB\npower off` → 400; регресс: config/health/nettools/bluetooth/index.

**Остаточные риски:** `/api/monitoring/<ip>` (SSRF на `:19999`) и
`/api/nettools/ports` (произвольный target) — features, но нужна валидация/белый
список при hardening (P1-10); hosts из `/api/network/config` (user-editable)
проходят ту же валидацию при проверке — несовместимые старые значения дадут 400
(поведение видимое, не тихое).

### 30.09.2026 — P0-6: синхронизация repo ↔ X96 — **DONE**

**Проблема (B6 + §5):** репозиторий и X96 разошлись в 19 файлах: сервер впереди
(CSRF-meta в `base_app.html`, retry в `recycling.py`, favicon в `login.html`),
репо впереди (`monitor.py` с ThreadPoolExecutor, `terminal.html` без испорченной
кодировки), 14 файлов различались EOL, на сервере — 7 junk-файлов
`templates/apps\*.html` (буквальный backslash). Риск: любой пуш/деплой «как есть»
ломает CSRF во вкладках или терминал.

**Изменения:**
- **server→repo:** `templates/base_app.html` (+24: csrf-meta + fetch-обёртка),
  `modules/recycling.py` (3 попытки запроса vitaminstir), `templates/login.html` (+1 favicon);
- **repo→server:** `modules/monitor.py` (ThreadPoolExecutor, timeout=3),
  `templates/apps/terminal.html` (восстановлен), 14 EOL-only файлов;
- **новое:** `.gitattributes` (`* text=auto eol=lf`, бинарные исключения);
- **удалено с сервера:** 7 файлов `templates/apps\*.html` → перемещены
  (не удалены) в `/tmp/junk-templates-20260930-034105/`;
- бэкапы: `templates/apps/terminal.html.backup-20260930-033734`,
  `modules/monitor.py.backup-20260930-033734`.

**Тесты (X96, `/tmp/test_p06_sync.py`, 13/13 PASS):** admin login; terminal.html
содержит xterm-код без мозги; `/monitoring` 200; `/api/monitoring/127.0.0.1` →
JSON с `cpu`/`ram` (get_system_overview); csrf-meta и fetch-обёртка есть в
вкладках; регресс `/`, filemanager, nettools, bluetooth, health.
Загруженные `.py` прошли `py_compile`, сервис перезапущен, active.

**Сверка md5 (финальная, `filelist_host.py` + compare):** exact=120,
EOL-only=0, content_diff=0, только на сервере=0 — **repo == X96 байт-в-байт**.

**Остаточные риски:** OP (Orange Pi) остался на старой версии (нет `modules/`) —
отдельная задача (дублирование сканов, остановка сервиса на OP); junk-файлы
лежат в `/tmp` (перезагрузка сотрёт — ок); `modules/*_b64.txt` в git — чистка P4.

### 30.09.2026 — P0-4: сейф — Fernet + миграция + can_edit — **DONE**

**Проблема (B4):** «шифрование» сейфа = `b64(HMAC[:16] || plaintext)` — пароли
лежали открытым текстом (base64), HMAC при расшифровке **игнорировался**
(`if h == raw: return text; return text`), все 4 API — только `@login_required`
(guest читал все пароли), неудачная расшифровка приводила к двойному шифрованию
при следующем сохранении.

**Изменения:**
- `modules/core_routes.py`:
  - `_secrets_fernet()` — ключ из отдельного файла `secret.key` (32 байта;
    64-байтовый hex → fromhex; иначе sha256-дедукция; chmod 600 при создании);
  - `_secrets_encrypt/decrypt` — Fernet (AES-128-CBC + HMAC-SHA256);
    fallback на legacy base64+HMAC с **реальной** проверкой HMAC;
  - `_load_secrets` — legacy-записи мигрируют в Fernet при первом чтении
    (с бэкапом `secrets.json.backup-<ts>`); нечитаемые → id в `_enc_ids`,
    пишутся обратно без изменений (без двойного шифрования);
  - `_save_secrets` — не мутирует вход, атомарная запись (tmp+`os.replace`),
    chmod 600;
  - 4 роута `/api/secrets*` → `@login_required` + `@can_edit` (guest → 403);
    create/update сбрасывают флаг `_enc_ids` (новый пароль шифруется);
- `templates/apps/passwords.html` — UI-гейт `isRoot` admin → `!= 'guest'`
  (соответствует can_edit), текст заглушки;
- `requirements.txt` — `cryptography>=42,<47` (venv на X96: pip install OK).

**Файлы:** `modules/core_routes.py`, `templates/apps/passwords.html`,
`requirements.txt` (repo == X96). Бэкапы `*.backup-20260930-034959`.

**Тесты (X96):**
- миграция `/tmp/test_p04_migration.py` **18/18**: legacy→Fernet (бэкап 1 раз,
  без повторной миграции), mode 600, битая legacy-запись сохраняется как есть
  (без double-encrypt), roundtrip, мутации не утекают в вызывающий код;
- HTTP `/tmp/test_p04_secrets.py` **29/29**: аноним 302/400, guest все методы→403,
  user GET/POST/DELETE→200 (can_edit), admin CRUD, на диске — Fernet-токен
  `gAAAA…` вместо plaintext, mode 600, обновлённый пароль тоже шифруется;
  регресс страницы/health. users.json (пароли user/guest) — бэкап→тест→восстановлен.
- финальное состояние: `secrets.json` = `[]`, mode 600; лог без ошибок.

**Остаточные риски:** пароль пользователя при смене передаётся в открытом виде
по HTTP (панель без TLS — вне scope P0, см. hardening); `_fernet_cache` кэширует
ключ в памяти процесса (ok); конкурентная запись `secrets.json` двумя
запросами theoretically possible (single-process, низкий риск) — при необходимости
file-lock в P1.

### 30.09.2026 — P0-5: auth — enabled/TTL/мин. длина/re-hash — **DONE**

**Проблема (B5):** `get_current_user` не проверял `enabled` — отключённый
пользователь продолжал работать со старой сессией («выключение» не действовало
до истечения куки); `_verify_hash` принимал SHA-256 навсегда (fast-hash без
salt); смена пароля — `len(pw) < 1`; TTL сессий отсутствовал; в `core_routes`
жили дубли `_hash/_verify_hash/load_users/save_users/get_current_user`
(контекст-процессор мог расходиться с декораторами).

**Изменения:**
- `modules/auth.py`:
  - `SESSION_TTL = 12*3600`; `get_current_user` — нет `login_ts` (старые
    сессии)/истёк TTL/`enabled=false`/нет юзера → `session.pop` + None
    (logout для всех декораторов и SocketIO);
  - логин: `session["login_ts"] = now`; legacy SHA-256-хэш → **однократный
    re-hash bcrypt при первом успешном входе** (без локаута);
- `modules/core_routes.py`:
  - дубли хелперов → `from modules.auth import ...` (единый источник);
  - `POST /api/users/<u>/password`: `len(pw) < 8` → 400
    (UI `sys-users/block.html` показывает `d.error` — без правок);
- `requirements.txt` не менялся.

**Файлы:** `modules/auth.py`, `modules/core_routes.py` (repo == X96).
Бэкапы `*.backup-20260930-035630`.

**Тесты (X96):**
- юнит `/tmp/test_p05_unit.py` **17/17**: нет login_ts/TTL истёк/отключённый
  → None + pop; валидная сессия → admin; sha256/bcrypt verify; core делегирует
  в auth (`is`-идентичность); min-8 в исходнике;
- HTTP `/tmp/test_p05_auth.py` **23/23**: отключение юзера «вживую» выбивает
  старую сессию (302) и после включения сессия остаётся мёртвой (re-login);
  отключённый не может войти; пароль 7 симв. → 400, 8 → 200; вход с SHA-256 →
  302 и хэш в users.json становится `$2b$…`; регресс pages/health/api/users.
  users.json: бэкап → setup → тест → **восстановлен**.

**Остаточные риски:** все существующие на момент апгрейда сессии выкидываются
(нет login_ts) — однократный ре-логин; SHA-256-аккаунты, ни разу не вошедшие,
остаются sha256 до первого входа (детект: `grep -v '$2' users.json`); TTL
фиксированный 12ч (конфигурируемый — P2 Configuration).

### 30.09.2026 — P1-7: схема БД — однократный init, user_version, индекс — **DONE**

**Проблема:** DDL и ALTER-миграции колонок выполнялись в `get_db()` на **каждом**
подключении (лишняя работа + шум в логе); `PRAGMA user_version = 0` (аудит §3 —
миграции не версионированы); нет индекса `events(ip, id)` — `/history` и
`/device/<ip>` (35 836 событий) делают полный скан; `con.close()` без `finally`
— при исключении коннект утекал (scan_loop, все 5 роутов, `weather_current`
в `app.py` и `core_routes`).

**Изменения:**
- `modules/devices_routes.py`:
  - `init_db_schema(force=False)` — весь DDL + миграции колонок +
    `CREATE INDEX IF NOT EXISTS idx_events_ip_id ON events(ip, id)` +
    `PRAGMA user_version = 1` (если `< 1`), под `threading.Lock`, идемпотентна
    (повторный вызов → `False`), коннект закрывается в `finally`;
  - `get_db()` — только connect + `busy_timeout`/WAL + авто-вызов init, пока
    не done (после init DDL не выполняется — проверено: число объектов
    в `sqlite_master` не меняется);
  - `scan_loop` — `con = None` до `try`, закрытие в `finally` (устранена утечка
    при `SCAN ERROR`); роуты `index/history/device/set_name/dismiss_new` —
    `try/finally` (в `device` not-found return тоже через finally);
- `app.py`: `init_db_schema()` в `__main__` до `start_scan_thread()`;
  `weather_current()` — `close` в `finally` (при исключении в запросе утекал);
- `modules/core_routes.py`: `weather_current()` — вложенный `try/finally`
  вокруг запроса.

**Файлы:** `modules/devices_routes.py`, `app.py`, `modules/core_routes.py`
(repo == X96 после деплоя). Бэкапы: `*.backup-pre-p07-20260930-070508` (до
правки, из git HEAD), `*.backup-20260930-070645`; БД
`devices.db.backup-p07-*` (integrity ok, 40 devices / 35 836 events).

**Тесты (X96, `/tmp/test_p07_db.py` 22/22 PASS):**
- prod: `user_version = 1`, индекс существует и используется
  (`SEARCH events USING INDEX idx_events_ip_id (ip=?)`), integrity ok,
  данные не потеряны (40 / 35 836 → 40 / 35 836);
- fresh-DB (tmp): init создаёт таблицы, все колонки, индекс, version; повторный
  init — no-op; `get_db()` работает без DDL; `sqlite_master` count не растёт;
- HTTP: admin login 302, `/` `/history` `/device/<ip>` 200, unknown 404,
  `set_name`/`dismiss-new` на несуществующий IP 302/200 (без мутаций), health 200;
- после рестарта в журнале: `DB SCHEMA INIT: version=1` + `SCAN OK` каждые 30 с
  (скан-поток жив), без traceback.

**Остаточные риски:** OP работает со старой версией кода (нет `modules/`) —
при синке получит init автоматически при старте; DDL других модулей
(currencies/inventory/recycling/weather-monitor) — свой idempotent init вне
`get_db()`, не трогался (полноценная миграционная система — PHASE 5 позже).

### 30.09.2026 — P1-8: фоновые задачи — guard'ы + удаление мёртвого/дублей — **DONE**

**Проблема:** `start_scan_thread()` без guard — повторный вызов дал бы второй
скан-поток; `/inventory/scan` и `/api/bluetooth/scan` без lock — повторный
POST → параллельные nmap/bluetoothctl-прогоны (race + лишняя нагрузка);
в `core_routes.py` жил **мёртвый** `init_background_tasks` (не вызывается
нигде) вместе с мёртвыми дублями `update_currencies/recycling_background`,
блоком alarm-helpers (`_alarm_scheduler` и др. — живая копия в `media_routes`)
и дублями `weather_current`/`check_internet*`/`page_data` из `app.py`
(два независимых кэша, два источника weather/internet); в `devices_routes`
— мёртвая пара `check_internet*`.

**Изменения:**
- `modules/devices_routes.py` — `start_scan_thread()`: `_scan_lock` +
  `_scan_thread`, повторный вызов возвращает уже запущенный поток
  (no-op); удалены мёртвые `check_internet/check_internet_cached` + `_inet_cache`;
- `modules/inventory_routes.py` — модульный `_inv_scan_lock` +
  `_spawn_inventory_scan()`: `acquire(blocking=False)` → при занятости
  `False` (маршрут по-прежнему 302), `release` в `finally`; импорт
  `modules.inventory` вынесен на уровень модуля;
- `modules/network_routes.py` — `_bt_scan_lock` + `_spawn_bt_scan()`
  (scan on → 30 c → scan off, `release` в `finally`); ответ
  `{"ok": true, "already_running": <bool>}`;
- `modules/core_routes.py` — удалены `init_background_tasks`,
  `update_*_background`, alarm-блок (`ALARM_FILE/_load_alarms/_save_alarms/
  _alarm_scheduler` — полностью живёт в `media_routes`),
  `weather_current/check_internet*/_page_data_cache/_inet_cache`;
  `page_data()` → делегат `app.page_data` (единый кэш и источник).

**Файлы:** `modules/{core_routes,inventory_routes,network_routes,devices_routes}.py`
(repo == X96 после деплоя). Бэкапы `*.backup-pre-p08-20260930-072054` (4 файла).

**Тесты (X96, `/tmp/test_p08_guards.py` 32/32 PASS):**
- unit-guard: scan (повторный вызов → тот же thread), inventory
  (True → False → после завершения True, ровно 2 прогона, lock свободен),
  bluetooth (True → False, lock захвачен, `scan on` отдан потоку);
- удаление: 8 проверок отсутствия символов в core/devices; `media_routes`
  живой (alarm scheduler + helpers на месте);
- делегация: `core.page_data() is app.page_data()` (один кэш), ключи
  weather/internet/interval/max_misses;
- HTTP: login 302; `/` `/apps` `/currencies` `/inventory` `/history` 200;
  `/inventory/scan` x2 → 302/302; bt-scan 1-й `already_running:false`,
  2-й **`already_running:true` (guard подтверждён на сервисе)**;
  `/api/alarms` 200; health 200;
- журнал после рестарта: `SCAN OK` каждые 30 с (один поток), без
  Traceback/ImportError.

**Остаточные риски:** UI inventory-скана не показывает прогресс (вне scope);
`system_routes`/`weather_routes` имеют локальные обёртки `page_data/
weather_current`, но делегируют логику в `app`/`weather` — не дублируют её;
мусорные `app_copy.py`/`patch_app.py`/`app_remote.py` не трогались.

### 30.09.2026 — P1-9: Observability — /api/health: version/uptime/last-discovery/db — **DONE**

**Проблема:** `/api/health` отдавал только `checks` (database/disk/ram/cpu_temp)
+ timestamp — ни версии панели, ни аптайма, ни статуса discovery, ни состояния
БД (§2 Observability: «состояние видно только через UI»).

**Изменения:**
- `app.py` — `APP_VERSION = "0.9.0"` и `_SERVICE_START = time.time()`
  (единый источник версии и аптайма процесса);
- `modules/devices_routes.py` — `_scan_status`
  (`last_scan/last_ok/last_error/errors`) обновляется в `scan_loop`
  (успех / ошибка / пустой вывод); `get_scan_status()` добавляет
  `interval_sec` и `thread_alive`;
- `modules/system_routes.py` — `/api/health`:
  - db-check одним коннектом → объект `db` `{status, path, size_bytes,
    journal_mode, user_version, devices, events}`;
  - новые поля: `version`, `uptime` `{host_sec (/proc/uptime), service_sec}`,
    `last_discovery` `{scan, ok, error, errors, interval_sec, thread_alive}`,
    `db`;
  - легаси-ключи `ok/checks/timestamp` и коды 200/503 не менялись.

**Файлы:** `app.py`, `modules/devices_routes.py`, `modules/system_routes.py`
(repo == X96 после деплоя). Бэкапы `*.backup-pre-p09-20260930-072700` (3 файла).

**Тесты (X96, `/tmp/test_p09_health.py` 22/22 PASS):**
- health 200/`ok=true`; legacy `checks` (database="ok"+disk/ram/cpu_temp) и
  `timestamp` в прежнем формате;
- `version == 0.9.0`; uptime: host 118128 c, service 9.8 c (service ≤ host);
- `last_discovery`: scan/ok в формате DD.MM.YYYY HH:MM:SS (первый скан
  после рестарта отработал), `thread_alive=true`, `interval_sec=30`,
  `errors=0`, `error=null`;
- `db`: status ok, `user_version=1`, `journal_mode=wal`, devices=40,
  events=35836, size 6.3 МБ;
- `get_scan_status()` доступен из другого процесса; регрессии: login 302,
  `/` `/history` `/system` `/api/system/health` 200; журнал — `SCAN OK`,
  без Traceback.

**Остаточные риски:** version — константа в `app.py` (в settings переедет
в P2 Configuration); `service_sec` считается от импорта app.py (≈ старт
процесса); unauth-доступ `/api/health` сохранён осознанно (мониторинг
без входа в закрытой LAN; роут добавлен ещё в P0-3-регламенте «по решению»).


---

### 30.09.2026 — P1-10: Безопасность окружения — заголовки/куки/чистка секретов + фикс публичной утечки demo — **DONE**

**Проблема:** публичный демо-слепок (`kotmartovskiy.github.io/Lan-discovery-demo`)
отдавал `SSH root / 1234` и реальную топологию `192.168.3.x` (включая имена
файлов `device-192.168.3.x.html`); панель не ставила security-заголовков и
cookie-флагов; 10 отслеживаемых скриптов содержали `password='1234'` и
рабочие IP.

**Изменения:**
- `app.py`: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE=Lax`,
  `SESSION_COOKIE_SECURE=False` (панель по HTTP в LAN — иначе вход сломается);
  after_request `_security_headers` → `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: same-origin` (setdefault,
  не дублирует чужие). CSP не вводится — CDN xterm/socket.io (residual).
- `templates/help.html`: `root / 1234` → «пароль задан при установке»;
  `(пароль: 1234)` → «пароль — от root-аккаунта…». IP-таблицы в исходнике
  сохранены (UX панели) — скрабятся при публикации.
- `tools/make_demo.py`: `scrub_net_secrets()` — `192.168.3.x → 192.168.1.x`,
  пароль-литералы → `••••`; применяется в HTML- и JSON-проходах **и в именах
  файлов** (`write_file`, ключи/значения `page_map` — синхронно со скрабом
  HTML до `transform`); логин генератора — env `LAN_PANEL_PASS` (без литерала).
- `tools/demo_lint.py`: `check_net_secrets()` — контент (html/json/js/css) и
  **имена файлов** на `192.168.3.` + паттерны `root/1234`, `пароль: 1234`,
  `password=1234`; позитив-контроль: старый слепок → 123 ошибки.
- Скрипты (env, без дефолтов): `deploy.py`, `deploy_templates.py`,
  `find_tv.py`, `install_xplore.py`, `remote_edit.py`, `ssh_query.py`,
  `tv_adb.py` → `LAN_SSH_HOST/LAN_SSH_USER/LAN_SSH_PASS` (+`LAN_TV_ADB`,
  `LAN_TV_IP`); `test_api_auth.py`/`test_status.py`/`test_sys.py` →
  `LAN_PANEL_PASS`.

**Деплой:** бэкапы `*.backup-pre-p10-20260930-074345` (app.py, help.html,
make_demo.py); демо регенерировано на боксе (`LAN_PANEL_PASS=…`), скачано,
`tools/demo_lint.py` → **LINT OK: 188 files, net/secret leaks: 0**.

**Тесты (X96, `/tmp/test_p10_env.py` 27/27 PASS):**
- заголовки на `/login`, `/api/health`, `/`, `/system`, `/static/style.css`;
- Set-Cookie: `HttpOnly` + `SameSite=Lax`, без `Secure`; login → 302;
- health: version 0.9.0, db {status ok, user_version 1, devices 40,
  events 35836}, uptime, last_discovery (P1-9-регрессия);
- `/help`: без `root / 1234` и `пароль: 1234`, IP-таблица сохранена (UX);
- регрессии: CSRF анонимный POST → 400, без сессии → 302, no-store на API,
  журнал без Traceback, `SCAN OK`.

**Публикация:** чистый слепок отправлен в `Lan-discovery-demo` (GitHub Pages)
— живая утечка `root/1234` + IP на публичном сайте закрыта.

**Остаточные риски:** root-PTY и dev-Werkzeug (`allow_unsafe_werkzeug`)
дефернуты вне scope задачи 10; IP в функциональных дефолтах кода
(`NETWORK`, monitoring/web-хосты, default hosts, `nettools` placeholder,
исходник `help.html`) — ок для приватного репо, скрабятся демо-пайплайном;
`patch_app.py`/`remote_edit.py` — исторические one-off (IP внутри payload);
`AGENTS.md` содержит SSH-креды осознанно (ops-файл приватного репо, не
публикуется; `sanitize_docs` его не трогает); CSP не введён; регенерация
демо требует `LAN_PANEL_PASS` в окружении.

### 30.09.2026 — P1-11: Изоляция сбоев — барьер отсутствующих подсистем + фикс фиктивного discovery — **DONE**

**Проблема:** панель не имела ни одного errorhandler; отсутствующий внешний
бинарь (nmap, iw, host, tracepath, smartctl…) давал грубый 500 с errno;
зависимости нигде не декларировались. В ходе задачи обнаружен скрытый баг:
на X96 discovery **фальшивил успех** — `SCAN OK: 4 devices` каждые 30с, при
этом nmap не был установлен вообще, а `run_scan` был захардкожен на интерфейсы
Orange Pi (`end0`/`wlan1`; на X96 — `eth0`/`wlan0`), поэтому реально сканировались
только `self_ips` — online держался 4 с 28.09 (реальные хосты заморожены в БД).

**Изменения:**
- `app.py`: `import shutil` + `from html import escape`; блок P1-11 перед Auth:
  `CAPABILITY_PROBES` (nmap, ping, tracepath, host, iw, bluetoothctl, smartctl,
  ffmpeg, mpv, lsblk) → `probe_capabilities()` → `app.config["CAPABILITIES"]` +
  warning `MISSING DEPS: …` при старте; глобальный
  `@app.errorhandler(FileNotFoundError)` → warning в журнал, `/api/*` → JSON 503
  `{ok:false, error:"не установлена зависимость: …", dependency}`,
  страницы → HTML 503 «функция недоступна» (экранировано, со ссылкой на главную).
- `modules/system_routes.py`: `current_app`; `/api/health` += `capabilities`
  (карта бинарей) и `missing_deps` (список отсутствующих) — легаси-ключи P1-9
  не тронуты.
- `modules/devices_routes.py`: `run_scan` — отдельный `except FileNotFoundError`
  → `log.error("SCAN: nmap не установлен …")` + `return None` (никакого
  фиктивного OK); ветка `output is None` в `scan_loop` → `last_error` +
  `errors += 1` (консистентно с обычной except-веткой); перебор интерфейсов
  `end0 → eth0` и `wlan1 → wlan0` с `break` при найденных хостах.
- На X96 установлена зависимость: `apt-get install nmap` (7.93).

**Деплой:** бэкапы `*.backup-pre-p11-20260930-082725` (app.py,
system_routes.py) + `*.backup-pre-p11-*` (devices_routes.py, два этапа),
py_compile OK, `systemctl restart lan-discovery` → active.

**Тесты (X96, `/tmp/test_p11_deps.py` 30/30 PASS):**
- Part A: capabilities == re-probe `shutil.which`; синтетические роуты с
  несуществующим бинарём → API 503 JSON (`ok:false`+`dependency`) и HTML 503;
- Part B: `PATH=""` — ping/dns/wifi-scan/inventory-scan/главная → **не 500**
  (ping/dns → `ok:false`, inventory → штатный 302); `run_scan()` без nmap →
  `None` (не строка self_ips);
- Part C: health в процессе — capabilities/missing_deps + все поля P1-9;
- Part D: живой сервис — health 200, `missing_deps: [tracepath, host]`,
  nosniff/X-Frame-Options (P1-10-регрессия);
- результат после фикса: `SCAN OK: 19 devices`, online 4 → 19, `last_seen`
  реальных хостов обновляется; журнал без Traceback/SCAN ERROR.

**Остаточные риски:** импорты Python-модулей остаются fail-fast (изоляция
импортов не вводилась — задокументировано); route-level broad except по-прежнему
возвращает 200 `ok:false` с текстом ошибки (криптический errno); барьер ловит
только `FileNotFoundError` (не OSError/TimeoutExpired); `tracepath`/`host` не
установлены и видны в `missing_deps` (демонстрация честной деградации);
`INVENTORY ERROR` печатается в лог фонового потока (не HTTP-статус).

### 30.09.2026 — P2: Configuration — хардкоды → `_cfg` (settings.json), тест override, docs — **DONE**

**Проблема (§2 «Configuration»):** сеть/скан/интерфейсы/порт были захардкожены
в коде при том, что `/etc/lan-discovery/settings.json` уже содержал
`network.subnet`, `scan_interval`, `max_misses`, `self_ips`, `web.flask_port` —
ключами никто не пользовались: настройки из UI «Система → Настройки»
сохранялись, но не применялись; `SCAN_INTERVAL`/`MAX_MISSES` дублировались в
трёх местах (app/devices/system); `app.NETWORK` был мёртвым; `monitor.py`
показывал трафик только `end0/wlan1` (на X96 — пусто).

**Изменения (задача 12):**
- `app.py`: удалены мёртвые `NETWORK` и константы `SCAN_INTERVAL`/`MAX_MISSES`;
  ленивые `_scan_interval()`/`_max_misses()` через `_cfg`; `/api/settings` и
  `socketio.run` → `_cfg("web", "flask_host"/"flask_port", …)`.
- `modules/devices_routes.py`: ленивые `_subnet()`/`_scan_interval()`/
  `_max_misses()` (правки применяются через 10-сек кэш, без рестарта);
  nmap-сканер и offline-логика читают их вместо констант.
- `modules/system_routes.py`: `page_data` (interval/max_misses) и мониторинг
  трафика → `_cfg("network", "traffic_ifaces", …)` (лейблы lan/wifi строятся
  правилом `wlan*` → wifi).
- `modules/inventory.py`: nmap-скан подсети → `_cfg("network", "subnet", …)`.
- `modules/network_routes.py`: `/api/wifi/scan` → перебор
  `_cfg("network", "wifi_ifaces", ["wlan1", "wlan0"])` (вместо двух разворотов).
- `modules/monitor.py`: трафик → `_cfg("network", "traffic_ifaces", …)`
  (дефолт покрывает `end0/eth0/wlan1/wlan0` — обе платформы).
- `modules/monitoring_routes.py`: self-check `/api/monitoring/<ip>` →
  `network.self_ips` вместо хардкода `192.168.3.243`.

**Деплой (задача 13):** бэкапы `*.backup-pre-p2-20260930-091449` (7 файлов),
py_compile OK, restart → active; тест `/tmp/test_p22_config.py` — **18/18 PASS**:
override-подмена settings (subnet `10.99.0.0/24`, interval 77, max_misses 9,
self_ips `10.99.0.5`, flask_port 9099) → все ленивые хелперы читают новые
значения; self-check по новому self_ips → 200 (локальный overview); после
восстановления — боевые 30/6/`192.168.3.0/24`; регрессии: health (P1-9/P1-11),
live-сервис, `SCAN OK: 8–15 devices`, журнал без Traceback.

**Документация (задача 14):** `docs/Конфигурация.md` — таблицы всех ключей
settings.json (кто читает, дефолт, когда применяется), отдельный `network.json`,
`server_ips` помечен «зарезервирован, кодом не читается», how-to «новая
сеть/платформа» + минимальный пример override; `tools/sanitize_docs.py` —
добавлена замена подсети `192.168.3.0/24 → 192.168.1.0/24`, прогон →
**leaks: 0, exit 0**.

**Остаточные риски:** пути `/opt|/etc|/srv` и `network_check.py` остаются
захардкожены (осознанно, задокументированы — правка только под другую структуру
установки); `network.json` исторически отдельный файл (не слит с settings);
`server_ips` в settings не читается кодом (задел); дубли `load_settings`
(app/core/system/weather) читают один файл, но имеют раздельные кэши (10 с);
смена `flask_port` требует рестарта сервиса и правки systemd-юнита/фаервола.

### 30.09.2026 — P3: Hardware abstraction — `core/hardware.py` (PHASE 3) — **DONE**

**Задачи 15–16 (PHASE 3).** Создан `core/hardware.py` — единая точка
hardware-абстракции (без зависимостей от app, module-level импорт безопасен):
- `thermal_zone_path()` — перебор `thermal_zone*/temp` с кэшем 60 с (не только
  `zone0`), `thermal_temp()` — °C или None;
- `hdd_device()` (первый `sd*` в `/sys/block`), `emmc_device()`/`sd_device()`
  (первая `mmcblk*` с `device/type` == MMC/SD, без boot-партиций);
- `board_model()` (device-tree → hostname), `detect_platform()` — кэшированный
  dict `{board, arch, system, emmc, sd, hdd, thermal_zone}`.

**Убранные хардкоды:**
- `modules/system_routes.py`: thermal в `/api/status`, `/api/system/health`,
  `/api/health` (checks.cpu_temp) → `thermal_temp()` (None → «unavailable»);
  io-ticks `(mmcblk2, sda)` → `(emmc_dev, hdd_device())` c guard пропуска;
  clone-watcher `_dd_progress_watcher` — устройство цели из cmdline `dd of=`
  (regex `of=/dev/(mmcblk\d+|sd[a-z])`) с fallback на `emmc_device()`;
  `lsblk SIZE /dev/sda` и `smartctl -a /dev/sda` → `hdd_device()` (нет диска →
  «диск не обнаружен», кэш size ставит «?» вместо вечного None); `api_health`
  += блок `platform`.
- `modules/monitor.py`: temp в `get_system_overview` → `thermal_temp() or 0`.

**Деплой:** бэкапы `*.backup-pre-p3-20260930-*` (system_routes, monitor;
app.py не менялся), py_compile 4 файлов OK, restart → active; тест
`/tmp/test_p31_platform.py` — **22/22 PASS**: unit (zone0/48.3°C, eMMC
`mmcblk2`, SD `mmcblk1`, hdd None, board «X96 Max», arch aarch64, кэш
detect), API (health.platform + регрессии P1-9/P1-11, status temperature,
system/health warnings, /api/disks smart → «диск не обнаружен», monitor
overview), live-сервис; журнал без Traceback, `SCAN OK: 6–8 devices`,
`MISSING DEPS: tracepath, host`.

**Остаточные риски:** thermal — только первая доступная зона (без агрегации
нескольких); clone-watcher при отсутствии `of=` в cmdline и без emmc молча
прекращается (прогресс недоступен, сама клонировка не затронута); платформенные
интерфейсы (`end0→eth0`, `wlan1→wlan0`) по-прежнему перебором в
`devices_routes.run_scan` (капабилити-уровень, не board-абстракция).

### 30.09.2026 — P6: Discovery engine — core/discovery.py + manual scan (PHASE 6) — **DONE**

**Задачи 17–20 (PHASE 6).** Движок сканирования вынесен из UI-роутов
в отдельный модуль:

- **`core/discovery.py` (новый):** `run_scan(subnet, ifaces)` — scanner:
  единый цикл по `network.scan_ifaces` (дефолт `end0/eth0/wlan1/wlan0`,
  вместо двух хардкод-списков wired/wifi), возвращает **raw-вывод nmap**
  (убран двойной парсинг: раньше run_scan парсил, синтезировал текст, а
  `parse_scan` парсил его обратно); None при отсутствии nmap (P1-11) или
  пустой сети; self_ips дописываются в том же формате. `parse_scan` —
  normalizer; `reconcile(con, current, now)` — вся DB-логика (upsert,
  NEW/ONLINE/OFFLINE/misses) с новым событием **MAC_CHANGED**; `scan_loop`,
  `_scan_status`/`get_scan_status`, `start_scan_thread` (guard P1-8).
- **`modules/devices_routes.py`:** только схема БД (`init_db_schema`/`get_db`)
  + роуты + реэкспорт движка — импорты `app.py` (`start_scan_thread`,
  `init_db_schema`) и `system_routes.get_scan_status` не изменились.
- **`POST /api/scan` (новый, admin-only):** one-shot ручной скан с
  опциональными `{subnet, ifaces}` (не пишет settings), возвращает
  `{ok, devices, stats, subnet}`; аноним → 403, мусорные ifaces → 400.
- **UI:** кнопка «🔍 Сканировать» в шапке `devices.html` (только admin);
  CSRF ставит глобальный fetch-wrapper `base.html`.
- **`docs/Конфигурация.md`:** строка `network.scan_ifaces`, ссылки на
  `core.discovery.*` вместо `devices_routes.*`.

**Деплой:** бэкапы `*.backup-pre-p6-*` (devices_routes, devices.html),
py_compile OK, restart → active; тест `/tmp/test_p61_discovery.py` —
**30/30 PASS**: unit (parse_scan 3 хоста/hostname/MAC-регистр; reconcile на
tmp-БД: NEW → без событий → MAC_CHANGED+сброс name → 6 пропусков → OFFLINE →
ONLINE; живой run_scan 22 хоста + self_ips), API (аноним 403, ifaces 400,
manual scan 22 devices + stats, кнопка в `/`, `/history`, health
`last_discovery.scan/thread_alive`, platform-регрессия P3), live-сервис
(`SCAN OK: 18–22 devices`, журнал без Traceback).

**Остаточные риски:** ручной скан не сериализован с фоновым (параллельный
nmap возможен — терпимо, busy_timeout защищает БД); выбор интерфейса/подсети
через API и settings, а не через UI-форму; хардкод интерфейсов `end0→eth0`
перебором закрыт (оставался после P3) — теперь единый `scan_ifaces`.

### 30.09.2026 — P7: Event engine — события v2, фабрика, API (PHASE 7) — **DONE**

**Задачи 21–24 (PHASE 7).** Система событий получила формализованный вид:

- **Схема events v2** (`modules/devices_routes.py`, `SCHEMA_VERSION = 2`):
  новые колонки `severity` (info/warning/critical), `source`
  (discovery/system/monitoring/user), `metadata` (JSON-текст); миграция —
  тем же паттерном PRAGMA table_info + ALTER, что и у `devices`;
  backfill старых строк по типу события (NEW/ONLINE → info, OFFLINE/
  MAC_CHANGED → warning, всё NULL → info/discovery); индекс
  `idx_events_event(event, id)`. Бэкап БД **до** миграции
  (`devices.db.backup-pre-p7-*`, integrity ok) — боевые 37 119 событий
  сохранены, `user_version=2`.
- **`core/events.py` (новый):** `EVENT_SEVERITY` (карта тип→severity),
  `add_event(con, ip, hostname, mac, event, source, metadata, severity)` —
  единственная точка INSERT (неизвестный тип → info, metadata → JSON),
  `list_events` (фильтры event/severity/ip/source, новые первыми),
  `event_to_dict` (metadata парсится обратно в dict).
- **`core/discovery.py`:** все 4 INSERT-а (NEW/ONLINE/OFFLINE/MAC_CHANGED)
  заменены на `add_event(..., timestamp=now)` — события автоматически
  получают severity и source=discovery.
- **`GET /api/events`** (login, в `devices_routes`): `?limit=&event=`
  `&severity=&ip=&source=`, JSON-лента с распарсенным metadata; аноним → 302.
- **UI:** колонка «Уровень» с цветовым бейджем (critical — красный,
  warning — оранжевый, info — серый) в `/history`.

**Деплой:** бэкапы файлов `*.backup-pre-p7-*` + бэкап БД до миграции
(в `p7_run.sh`: sqlite3 `.backup` через python, integrity ok), py_compile
OK, restart → active; тест `/tmp/test_p71_events.py` — **30/30 PASS**:
unit (карта severity, add_event override/metadata JSON, list_events-фильтры,
reconcile NEW → info/discovery и MAC_CHANGED → warning), миграция боевой БД
(колонки, backfill без NULL, 37 119 событий, оба индекса), API (аноним 302,
limit/fallback, фильтры severity=warning и event=NEW, `/history` с «Уровень»,
страница устройства), регрессии P6 (кнопка скана) и P3 (platform), live
health `user_version=2` (первый прогон упал на Part D — сервис биндил порт
на 4-й секунде после рестарта; в чек добавлен retry — прошёл).

**Остаточные риски:** события пишет только discovery (system/monitoring
пока молчат — подключаются одной строкой `add_event`); `metadata` не
индексируется (поиск по нему — полный проход); IP_CHANGED/disk_warning не
реализованы (отложены до нужды — карта и фабрика к ним готовы); уведомления
(notify) — отдельная фаза.

### 30.09.2026 — P5: Database — миграции, ensure-таблицы, retention (PHASE 5) — **DONE**

**Задачи 25–28 (PHASE 5).** Схема БД получила формальную миграционную
систему и полноту на чистой установке:

- **Нумерованные миграции** (`modules/devices_routes.py`): карта
  `MIGRATIONS = ((1, _migration_v1), (2, _migration_v2))` — шаги применяются
  строго по `PRAGMA user_version` (не по table_info-эвристике), каждый шаг —
  своя функция с логом `DB MIGRATION: applied vN`. Шаг v1 — базовая схема
  devices/events (старый DDL) + индекс `events(ip,id)`; шаг v2 — колонки
  devices (hostname/is_new/misses/name/device_type), события v2
  (severity/source/metadata), backfill и `idx_events_event(event,id)`.
  На чистой БД путь 0→1→2 исполняется пошагово (подтверждено тестом);
  на боевой (уже v2) шаги пропускаются. Старый монолитный блок
  (CREATE + «if колонка отсутствует») заменён шагами.
- **Ensure 8 таблиц (P5-26):** `_ensure_extra_tables()` — точный DDL из
  боевой БД для таблиц, чьи CREATE жили только на серверах: `env_data`,
  `mchs_alerts`, `weather_alerts`, `weather_daily`, `weather_forecast`,
  `weather_forecast_history`, `weather_hourly`, `weather_observations` +
  уникальный индекс `idx_weather_observations_timestamp_unique`.
  Их читает `weather_routes`/`app.py`, пишет `deploy/weather-update.py` —
  до P5 восстановление на чистой машине давало неполную схему
  (закрыт §2-gap «8 таблиц weather не имеют CREATE в репо»).
- **Retention (P5-27):** настройка `events.retention_days` (default 180,
  `0` = выкл) → `core/events.cleanup_old_events(con, days)` — парсинг
  `DD.MM.YYYY HH:MM:SS` в Python (строковое сравнение дат тут невозможно),
  DELETE батчами по 500 id, коммит внутри. Чистка выполняется при старте
  сервиса (в `init_db_schema`, ошибки не валят init) и ежедневно фоновым
  daemon-потоком `retention_loop` (запуск в `app.py __main__` рядом с
  остальными потоками).
- **`docs/Конфигурация.md`:** секция `events` с ключом `retention_days`.

**Деплой:** бэкапы файлов `*.backup-pre-p5-*` (app.py по регламенту) +
**бэкап БД до рестарта** (`devices.db.backup-pre-p5-*`, integrity ok);
py_compile OK, restart → active; тест `/tmp/test_p51_migrations.py` —
**24/24 PASS**: пошаговость (v1 без severity/v2 с ними, шаг не ставит
версию сам), полный init на чистой БД (`DB MIGRATION: applied v1/v2`,
user_version=2, ядро + 8 ensure-таблиц + 3 индекса), retention unit
(2 старых удалено / свежее цело / days=0 → выкл), боевая БД (все таблицы,
integrity ok, 37 129 событий и 40 устройств целы), API (health
`db.user_version=2`, `/`, `/history`, `/api/events`), live health.

**Остаточные риски:** идентичность устройства = IP (MAC+IP+hostname —
отложено, отдельный крупный рефакторинг); retention чистит только `events`
(другие растущие таблицы: `weather_forecast_history` 16 693 строк,
`currency_history` — под вопросом, нужны ли); DDL остальных таблиц
(currency/inventory/recycling) создаётся ленивыми init их модулей, а не
единой точкой — при чистом старте порядок зависит от вызова
`init_inventory_db`/first-use; временные метки событий строковые —
retention парсит их в Python.

### 30.09.2026 — P13: Testing — pytest-структура + CI (PHASE 13) — **DONE**

**Задачи 29–31 (PHASE 13).** Ad-hoc проверки предыдущих фаз
(`/tmp/test_p*`, живая панель, admin/1234) превращены в репозиторный
pytest-набор + CI:

- **Структура (P13-29):** `tests/conftest.py` — фикстуры `events_con`
  (tmp-схема events v2), `devices_db` (полный init на tmp-БД с
  monkeypatch `DB`/`_init_done`), `no_dns` (заглушка DNS в reconcile).
  `tests/unit` — без сети и панели; `tests/live` — маркер `live`
  (фикстуры `panel_url` скипают прогон при недоступности, `admin_session`
  — CSRF-логин сессией). `pytest.ini`: по умолчанию `-m "not live"`
  (unit-прогон детерминирован; live — явно `pytest -m live` или
  `LAN_PANEL_URL=... pytest -m live`). `requirements-dev.txt`:
  `pytest>=8,<10` (проверено на 8.4.2 локально и 9.1.1 на X96).
- **42 unit-теста (P13-30):**
  - `test_discovery.py` (15): `parse_scan` (hostname/IP/MAC/vendor, пусто,
    без MAC-блока), `run_scan` — subprocess замокан (self_ips дописываются,
    не дублируются, FileNotFoundError → None, все ifaces упали → None,
    пустой вывод → только self_ips); `reconcile` — NEW → повтор (без
    ONLINE-дубля, appearances+1) → пропуски (misses растут, OFFLINE на
    пороге `max_misses`, severity=warning) → возврат ONLINE-события;
    MAC_CHANGED (обнуление name, событие warning) и case-insensitive
    сравнение MAC; форма `get_scan_status`; guard `start_scan_thread`
    (повторный вызов → тот же поток).
  - `test_events.py` (10): severity-карта и override, персистентность
    колонок, фильтры `list_events`, порядок (новые первыми), JSON
    metadata (включая кириллицу и битый JSON без падения), retention unit
    (старое удалено/свежее цело/0=выкл/мусорная дата не роняет).
  - `test_hardware.py` (6): переносимые проверки Win/CI/X96 — path/None,
    типы, кэш `detect_platform`, форма словаря.
  - `test_migrations.py` (6): шаги v1/v2 изолированно, идемпотентность
    повторных вызовов (с commit — бэкфилл+индекс в одной транзакции),
    полный init на чистой БД (user_version=2, ядро + 8 ensure-таблиц +
    индексы), повторный init → False, `_retention_days` через
    monkeypatch `load_settings` (180/0/30).
  - `test_config.py` (5): `_cfg` default/секция/missing-key,
    `_scan_interval` (0 → default 30), `_max_misses` (default 6).
- **5 live-тестов:** форма `/api/health` (db.version=2, platform,
    capabilities, last_discovery), анонимный `/api/events` → 302, логин +
  авторизованный `/api/events`, страницы `/`/`/history`/`/currencies`/
  `/apps`, `/api/currencies`.
- **CI (P13-31):** `.github/workflows/ci.yml` — push/PR в main →
  ubuntu + Python 3.11 + pip cache → `pip install -r requirements.txt -r
  requirements-dev.txt` → `pytest tests/unit -v` + py_compile
  app/core/modules; timeout 15 мин.

**Проверки:** локально (Windows, py3.12, pytest 8.4.2) — 42 unit PASS,
5 live PASS (панель 192.168.3.243:8080 отвечает); на X96
(`/opt/lan-discovery/tests`, venv, pytest 9.1.1) — 42 unit PASS,
5 live PASS. Нюансы: ручной вызов `_migration_v2` без `commit` теряет
бэкфилл/индекс (в `init_db_schema` commit есть — тесты учли); MAC в
reconcile обновляется как есть (регистр ≠ смена).

**Остаточные риски:** live-тесты гоняются только при достижимой панели
(по умолчанию выключены); ад-hoc deploy-скрипты фаз (`/tmp/test_p*`) не в
git; негативные проверки (403/валидация форм) покрыты частично.

### 30.09.2026 — P9: Installer — install.sh (PHASE 9) — **DONE**

**Задачи 32–34 (PHASE 9).** Развёртывание на чистой машине перестало
быть ручной процедурой:

- **`install.sh`** (корень репо, идемпотентен): preflight (python3 ≥ 3.9,
  наличие кода, root при необходимости) → code (копирует код в `--prefix`,
  если запуск из другого каталога; существующий `app.py` не перезаписывает
  — обновление отдано PHASE 10) → deps (недостающие пакеты через
  `dpkg -s`: nmap traceroute dnsutils iw bluez smartmontools ffmpeg mpv
  python3-venv; `--skip-apt`) → venv + pip -r requirements → config
  (базовый `settings.json` с авто-`subnet`/`self_ips` из `ip -4 addr`,
  только если файла ещё нет) → db (`init_db_schema()`: миграции/ensure/
  retention, из префикса) → unit (шаблон `deploy/lan-discovery.service` →
  `sed {PREFIX}` → `daemon-reload` + `enable --now`, только для /etc-юнита
  и при наличии systemd) → health (curl `/api/health` retry 30×2с, иначе
  die с подсказкой journalctl) → отчёт (admin/1234 — сменить пароль).
  Флаги: `--prefix`, `--unit-dir`, `--skip-apt`, `--no-enable`,
  `--dry-run` (печатает план любых изменений), `--help`.
- **`deploy/lan-discovery.service`** — шаблон действующего юнита (ExecStart
  с `{PREFIX}`), теперь юнит живёт и в репо (раньше только на сервере).
- **`docs/Установка.md`** — новая секция «Быстрая установка на чистую
  машину» (таблица шагов, флаги, первый вход, ограничение `--prefix`),
  обновлена «Проверка после установки» (`systemctl` + `/api/health`
  `db.status=ok, user_version=2`), требования разделены на
  «чистая установка» (Debian 11+/Armbian, python 3.9+) и боевую (X96).

**Проверки на X96:** `bash -n` (синтаксис), `--help`, `--dry-run` в
tmp-префиксе (печатает план, ничего не меняя), затем `/tmp/p9_run.sh`:
прогон 1 в чистый `/tmp/laninst-test` (код скопирован из /opt, venv
создан, pip, config — «уже есть» (боевой файл не тронут), db-init no-op,
юнит записан в tmp-unit с ExecStart=tmp, systemd-шаг пропущен, health
OK) → RUN1_RC=0, ARTIFACTS OK, UNIT PREFIX OK; прогон 2 («уже есть» на
code/venv/config, повторные шаги no-op) → RUN2_RC=0; состояние
(settings.json md5, юнит md5, user_version, count devices)
**не изменилось после обоих прогонов**. Найденные и исправленные по ходу
баги: `summary` возвращал 1 под `set -e` из-за `[[ ]] &&` (в dry-run
последней командой был false-тел), счётчик событий в сравнении
состояния — рост от живого скан-потока, а не от установщика (исключён из
cmp).

**Остаточные риски:** полный e2e «чистой машины» не воспроизведён — нет
Docker/WSL ни на боксе, ни локально (проверка = tmp-префикс + dry-run +
идемпотентность); пути `devices.db`/`settings.json` захардкожены в 9+
модулях — нестандартный `--prefix` работает только для кода/venv/юнита
(env-переопределение — отдельная задача, не в PHASE 9); apt-ветка
(`step_deps`) прогнана только в режиме «все пакеты есть»/`--skip-apt`;
ветка создания `settings.json` (чистая машина) — только dry-run
(на X96 файл существует → «уже есть»).

### 30.09.2026 — P10: Update/rollback — update.sh (PHASE 10) — **DONE**

**Задачи 35–37 (PHASE 10).** Обновление кода получило бэкап, проверку
и откат:

- **`update.sh`** (корень репо): backup (`tar` кода: app.py, core/,
  modules/, templates/, static/, games/, tools/, deploy/, requirements*,
  install/update/pytest, tests/ + `settings.json` + sqlite-бэкап
  `devices.db` → `/var/backups/lan-discovery/<YYYYMMDD-HHMMSS>/` c
  `meta.json` (ts/from/git-rev), ротация `--keep` default 5) → apply
  (`--from DIR` копированием по CODE_ITEMS; иначе `git pull --ff-only`
  если есть `.git`) → verify (`py_compile` app.py+core+modules
  python-хередоком с абсолютными путями) → pip -r requirements →
  `systemctl restart` → health (`/api/health` retry 30×2с).
  **Авто-rollback** при провале verify/health: распаковка бэкапа этого
  запуска (код + settings + restore БД) + restart + контрольный health;
  при health-сбое, если откат удался — «система здорова, обновление
  отменено» (rc=1), если нет — «срочная диагностика» (rc=1). Ручной
  `--rollback [TS]` (без TS — последний бэкап). Флаги: `--from`,
  `--rollback [TS]`, `--prefix`, `--keep`, `--dry-run`, `--help`.
- **`docs/Обновление.md`** (install.sh уже ссылался): два способа
  (update.sh / git pull), таблица шагов, флаги, устройство авто-отката,
  где бэкапы, ручной откат с предупреждением о потере событий после
  бэкапа, проверка после обновления.

**Тесты на X96 (`/tmp/p10_run.sh`, очищенный `/var/backups`):**
dry-run (rc=0, бэкапов 0, app.md5 не изменился) → success `--from`
(копия боевого кода: rc=0, бэкап 1, app.md5 исходный, health ok) →
**fail-путь** (в источник добавлен `def broken(:` → verify падает →
авто-rollback: `app.py` **восстановлен до исходного md5**, health ok,
rc=1, бэкап 2) → ручной `--rollback` (rc=0, app==original, health ok) →
`--keep 2` (rc=0, ротация до 2 бэкапов, health ok). Итоговый md5
`app.py` равен исходному — система ни разу не осталась в битом
состоянии. Найденные по ходу баги: `backup_path` возвращал полный путь
(двойная конкатенация) и regex `[0-9]{12}` не матчил дефис в формате
`YYYYMMDD-HHMMSS` (ручной откат молча падал); тест-скрипт не
восстанавливал `SRC/app.py` после fail-прогона; `verify` зависел от cwd
(исправлено на абсолютные пути из argv).

**Остаточные риски:** семантических версий нет (трассировка — git-rev в
`meta.json`); `--from` не удаляет файлы, исчезнувшие из новой версии
(копирование поверх — старые файлы остаются до ручной чистки);
health-fail-ветка авто-отката не воспроизводилась искусственно (только
verify-fail); git-путь (`git pull`) прогнан только dry-run'ом (боевой
каталог не git-репо).

### 30.09.2026 — PHASE 11 Backup/Recovery (задачи 38–41)

**Что сделано:**

- **`deploy/backup-db.sh` (задача 38)** — после sqlite-копии
  (`src.backup()`) теперь тарит `/etc/lan-discovery` в
  `/srv/backup-db/config_<ts>.tar.gz` (settings.json, users.json,
  secret.key, modules.json, notes/, secrets/), проверка
  `tarfile.is_tarfile` перед публикацией, ротация 14 дней по обоим
  паттернам (`devices_*.db` + `config_*.tar.gz`), логи
  `DB BACKUP OK` / `CONFIG BACKUP OK`. Рабочая копия таймера
  `/usr/local/sbin/backup-db.sh` обновлена (бэкап
  `*.backup-pre-p11-*`), дубликат в `deploy/` для git. UI-список
  (`DB_BACKUP_PATTERN=devices_*.db`, system_routes.py:13) tar не
  подхватывает.
- **`recovery.sh` (задача 39)** — восстановление из трёх источников:
  код (последний `code.tar.gz` из `/var/backups/lan-discovery/`),
  конфиг (последний `config_*.tar.gz` → `--config-dir`), БД (последний
  `devices_*.db` → `$PREFIX/devices.db`); авто-pick или явные
  `--db/--code-tar/--config-tar`; verify: `py_compile` системным
  python3 по app.py/core/modules + sqlite `integrity_check`,
  `user_version`, `COUNT(devices)>0`; `--unit` + `--unit-dir`
  (шаблон `deploy/lan-discovery.service` → sed `{PREFIX}`, юнит в
  drill-режиме не трогает `/etc/systemd/system`), `--no-restart`,
  `--dry-run`. Найденные по ходу баги: `ls -1` в multi-arg режиме
  выдавал заголовки `path:` (заменено на `ls -1d`); двойная
  склейка пути каталога-бэкапа (`bdir` уже полный) → пустой
  `CODE_TAR` и молчаливый `die`; `[[ ]] &&` в `main` заменён на `if`
  (set -e-ловушка); dry-run не должен создавать tmp-каталог.
- **Дрил (задача 40)** — `/tmp/p11_run.sh`: свежий бэкап
  (devices_*.db + config_*.tar.gz: tar tzf ok, settings/users/
  secret.key внутри, integrity ok); UI-фильтр venv-питоном не видит
  tar; recovery dry-run rc=0; drill в `/tmp/lanrec` (`--config-dir
  /tmp/lanrec/etc --unit --unit-dir /tmp/lanrec/unit --no-restart`):
  rc=0, `SYNTAX OK (22 files)`, `DB OK (integrity=ok user_version=2
  devices=40 events=37184)`, юнит с `ExecStart=/tmp/lanrec/venv/...`,
  settings.json md5 == боевому; повторный прогон идемпотентен
  (md5 стабилен); боевые settings/unit/health после дрила целы —
  **PHASE 11 PASS**.
- **`docs/Восстановление.md` (задача 41)** — где что лежит (таблица
  источников), восстановление на рабочей и на чистой системе
  (`install.sh` → `recovery.sh --unit`), флаги, что verify-ит
  скрипт, крипт дрила, troubleshooting.
- **`tests/unit/test_backups.py`** — 4 теста фильтра `db_backup_list`
  (tar/text-файлы не попадают, пустой/несуществующий каталог, поля
  и `size_human`); юнит-набор стал **46/46** (X96 venv и локально).

**Тесты:** юнит 46/46 на X96 и локально; дрил P11 PASS (см. выше);
`bash -n` обоих скриптов. CI после push.

**Остаточные риски:** `--prefix` восстанавливает только код/БД/конфиг —
хардкоженные пути `SETTINGS_PATH`/`DB` в модулях не переносятся на
чужой префикс (env-override — отдельная задача); восстановление на
**полностью чистую** машину прогнано только как
`install.sh`-примесно (сам дрил шёл поверх установленной системы с
изолированным префиксом); `config_*.tar.gz` не виден в UI-restore
(только recovery.sh).

### 30.09.2026 — PHASE 14 Documentation 1.0 (задачи 42–46)

**Что сделано:**

- **`docs/API.md` (задача 42)** — полный каталог всех **163 роутов**,
  сгенерирован из кода (`app.py` + `modules/*.py`): колонки
  метод/путь/доступ/описание, 10 секций по модулям. Фактология по
  доступу из декораторов: 140 под `login_required`, 40 под
  `admin_required` (20 пересекаются), **открытые ровно 3** —
  `GET/POST /login`, `GET /logout`, `GET /api/health`. Шапка дока —
  про CSRF (скрытое поле / `X-CSRFToken`) и 400/302 для анонима.
- **`docs/Безопасность.md` (задача 43)** — модель доступа (роли,
  декораторы, сессии, CSRF-обёртки в `base.html`/`base_app.html`),
  таблица секретов (`users.json`/`secret.key`/`secrets/`/`settings.json`),
  периметр (0.0.0.0:8080, нет TLS, dev-сервер `allow_unsafe_werkzeug=True`
  — gunicorn отложен в PHASE 8), сделанный hardening (csrf-meta во всех
  шаблонах, debug=False, bcrypt, тесты аноним-поведения в
  `tests/live/test_live_smoke.py`), **осознанные ограничения** (нет
  rate-limit/TLS, SocketIO вне CSRF, admin-роуты = контроль ОС) и
  чек-лист для новых роутов.
- **`docs/Архитектура.md` (задача 44)** — приведён в соответствие с
  кодом: раздел «Ядро — core/» (hardware/discovery/events/
  module_loader/module_catalog), **14 таблиц БД** (было 11) с группами
  и `user_version=2`, миграции `MIGRATIONS`/`_ensure_extra_tables` —
  в `modules/devices_routes.py` (не в app.py, как было написано),
  `retention_loop` в фоне, новый раздел «Эксплуатация: скрипты
  жизненного цикла» (install/update/recovery/backup-db + deploy.py),
  «Тесты и CI» (46 unit + live, CI py3.11), блок «Безопасность»
  перелинкован в новую страницу.
- **README + Home + `_Sidebar` (задача 45)** — таблица docs из **13
  страниц** (добавлены Обновление/Восстановление/Конфигурация/API/
  Безопасность), быстрый старт через `install.sh`/`update.sh`/
  `recovery.sh`, структура репо (core/, tests/, скрипты, CI),
  навигация wiki (сайдбар дополнен), ключевые файлы Home дополнены
  lifecycle-скриптами.
- **Сверка комплекта (задача 46)** — линкер-проверка всех относительных
  и wiki-ссылок в docs/README/AGENTS: **74 ссылки, 0 битых**;
  `pytest tests/unit` 46/46. **`tools/sanitize_docs.py` починен**: две
  давние «утечки» (`` `admin` / `1234` `` в Установка.md — добавлена
  REPL-замена) и структурный баг — `PAGES`-список wiki-страниц не
  содержал новых страниц (Обновление/Восстановление/Конфигурация/API/
  Безопасность) → `/wiki/` оставался в публичной копии; замена сделана
  regex-ом по существующим `docs/<page>.md`. Итог: **leaks 0, exit 0**,
  публичная копия `docs-public` (15 файлов) обновлена и залита в
  `Lan-discovery-docs`.

**Тесты:** линкер 74/0, sanitize exit 0, pytest 46/46 (код не менялся).

**Остаточные риски:** `docs/Модули.md` (227 строк) дублирует часть
информации `docs/API.md` — синхронизируются вручную при добавлении
роутов; wiki-страницы в GitHub-wiki обновляются отдельным пушом
(сами `docs/` — источник); английской версии комплекта нет (весь
проект русскоязычный).

### 30.09.2026 — PHASE 8 Security hardening: тесты + systemd + threat model — **DONE**

**Контекст:** P0 (задачи 1–6) и P1-10 (заголовки/куки/чистка секретов)
закрыты ранее; в PHASE 8 оставались два дефернутых пункта — root-PTY и
dev-Werkzeug, плюс отсутствие security-регресса в репо и systemd-юнит
без ограничений.

**Что сделано:**

- **`tests/unit/test_security.py` (задача 47)** — 8 тестов через Flask
  `test_client` (гоняются в CI, без живой панели): nosniff/
  X-Frame-Options/Referrer-Policy на `/login`, `/api/health`,
  статике; cookie `HttpOnly`+`SameSite=Lax` и **без** `Secure`
  (иначе вход по HTTP сломается); `no-store` на API; аноним `/` → 302
  `/login`; анонимный `GET /api/settings` → 302/401; mutating POST
  без CSRF → 400 (CSRF временно включается в фикстуре и
  восстанавливается). Набор unit вырос **46 → 54**.
- **`tests/live/test_live_security.py` (задача 48)** — перенос ad-hoc
  `/tmp/test_p10_env.py` (27 чеков P1-10) в pytest-стиль: заголовки
  на `/login`/API/`/`,`/system`,`/static/style.css`, флаги Set-Cookie
  на реальном POST /login, чистый `/help` (нет `root / 1234` и
  `пароль: 1234`, UX-таблица `192.168.3.243`/`X96 Max` сохранена),
  POST /login без токена → 400, `/api/status` без сессии → 302,
  поля health (P1-9-регресс). **10/10 PASS** против панели X96.
  Файл переименован из `test_security.py` → `test_live_security.py`:
  имена модулей pytest в `tests/unit` и `tests/live` конфликтовали
  («import file mismatch»), в стиле каталога с `test_live_smoke.py`.
- **systemd-хардening (задача 49)** — `deploy/lan-discovery.service`
  дополнен: `PrivateTmp=yes`, `ProtectKernelTunables=yes`,
  `ProtectKernelModules=yes`, `ProtectControlGroups=yes`,
  `LockPersonality=yes`, `RestrictRealtime=yes`, `WorkingDirectory`
  (шаблон с `{PREFIX}`); комментарий о сознательно НЕ включаемых
  (`ProtectHome` — filemanager/терминалу нужен `/root`;
  `SystemCallFilter`/`CapabilityBoundingSet` — риск сломать
  systemctl/диск/сеть). На X96: бэкап юнита
  `*.backup-pre-p8-*` → применение (`sed {PREFIX}`) →
  `systemd-analyze verify` чист → `daemon-reload` → restart → health
  200, флаги активны (`systemctl show` → yes/yes/yes/yes).
- **Функциональный смоук после рестарта:** login 302, filemanager
  `/root` → 200 (root-доступ не сломан), `/api/status` 200,
  `POST /api/scan` → 200 `{devices:17, ok:true}` (35.5с — эндпоинт
  синхронный, 15-сек таймаут первого прогона был просто мал),
  health 200 после скана. Live-набор: **15/15** (5 smoke + 10
  security).
- **Threat model «root by design» (задача 49)** — `docs/Безопасность.md`:
  новый раздел «Модель угроз» (почему root: systemctl/диск/eMMC/
  bluetooth/терминал; компенсации: admin-only, bcrypt+TTL-сессии,
  LAN-only, systemd-ограничения; что осознанно не включено и почему),
  зафиксирован dev-Werkzeug `allow_unsafe_werkzeug=True` как
  осознанное решение; раздел «Что уже сделано» дополнен юнитом и
  security-тестами.
- **ROADMAP:** §2 — «Security: заголовки/HOST» и «Security: секреты в
  git» (закрыты P1-10), «Stability: systemd» (закрыта через P8);
  §4 B1 — root-PTY/dev-Werkzeug закрыты как threat model; §7 PHASE 8
  → **DONE**.

**Тесты:** unit 54/54 (локально + CI), live 15/15 на X96,
`systemd-analyze verify` чист, смоук зелёный.

**Остаточные риски:** CSP не введён (CDN xterm/socket.io); альтернатива
«non-root + gunicorn» не реализована (требует переработки SocketIO +
sudo-моста — зафиксировано в docs); `ProtectHome`/syscall-фильтры
выключены осознанно (root by design); brute-force `/login` из LAN без
rate-limit.

### 01.10.2026 — PHASE 4: деплой новой версии на Orange Pi — **DONE**

**Контекст:** repo == X96 достигнуто ещё в §5, но Orange Pi
(armv7l/Debian 13) продолжал работать на старой монолитной версии
(app.py 566 строк, без `core/`/git/lifecycle-скриптов, юнит на
системном python) — «две версии в проде» и двойное сканирование LAN.

**Что сделано:**

- **Задача 51 — бэкапы:** сервис остановлен; в `/root/p4-backups/`
  (`*.backup-pre-p4-20261001-*`): `lan-code-*.tar.gz` (4.2MB, без
  venv/backups/БД — первая попытка tar застряла на локальных 988MB
  бэкапах OP, исключены), sqlite backup `devices.db` (5.6MB),
  копии юнита и `/etc/lan-discovery/`. Схема БД: `user_version=0`,
  15 таблиц — миграции в новом коде аддитивные (CREATE IF NOT EXISTS +
  ALTER ADD COLUMN с проверкой колонок), перенос безопасен.
- **Задача 52 — деплой:** `git archive` HEAD (1.5MB) поверх
  `/opt/lan-discovery` (md5 `app.py` = repo). **Открытие:** на armhf
  (armv7l) у `cffi`/`bcrypt`/`cryptography` нет manylinux-wheel —
  pip падал на сборке cffi (`No arm-linux-gnueabihf-gcc`).
  **Решение:** системные `python3-{cffi,cryptography,bcrypt}` из
  Debian 13 (1.17.1/43.0.0/4.2.0 — удовлетворяют пинам
  requirements) + `python3 -m venv --system-site-packages`, всё
  остальное (Flask 3.1, Flask-SocketIO 5.5, Flask-WTF, pytest…) —
  чистый pip (pure wheels). `install.sh --skip-apt`: config не тронут
  (settings.json существует), `init_db_schema()` → миграции
  **v0→v2** (`DB SCHEMA INIT: version=2`, данные: devices=40,
  events=36782 — на месте), юнит по шаблону (venv + хардening P8),
  enable --now → health OK. `install.sh` починен для armhf
  (step_deps += python3-cffi/cryptography/bcrypt, step_venv +=
  `--system-site-packages`) — чистая установка на armhf теперь
  работает; существующие установки не затронуты (venv не
  пересоздаётся).
- **Задача 53 — верификация:** md5 `app.py` == repo;
  **unit 54/54 PASS** на OP (Python 3.13.5/armv7l); смоук —
  login 302 / status 200 / health 200 / filemanager `/root` 200 с
  127.0.0.1 и 192.168.3.235 (`.234` — No route to host: известный
  отвал LAN-кабеля, не регресс); повторный вход после bcrypt-rehash
  работает; `journalctl` чист; `settings.json`/`secret.key` не
  тронуты, `users.json` — admin-hеш стал bcrypt `$2b$12$…` —
  **штатный lazy rehash P0-5** при первом входе (user/guest остались
  SHA-256); доставлен `iputils-tracepath` (стартовый WARNING
  `MISSING DEPS: tracepath` ушёл, health: tracepath true).
- **Решение оператора:** обе панели активны и сканируют LAN каждые
  30с (**двойное сканирование оставлено** — в `_scan_interval()`
  `0` превращается в `30` через `or 30`, отключения авто-скана в
  коде нет; менять не стали).
- **ROADMAP:** §2 «Переносимость (X96)» → закрыта; §6 задачи 51–54
  DONE; §7 PHASE 4 → **DONE**.

**Тесты:** unit 54/54 на обоих узлах (X96 aarch64 + OP armv7l);
health/login-смоук на OP зелёный.

**Остаточные риски:** двойное сканирование LAN (осознанно);
`LAN .234` недоступен до физического восстановления кабеля; на OP
два пустых легаси-БД (`events.db`, `lan.db`) оставлены как есть;
пакетная сборка cffi из sdist на armhf без компилятора по-прежнему
невозможна — armhf-окружение обязано идти через apt-зависимости.

### 01.10.2026 — PHASE 15 Production 1.0 (тег v1.0.0) — **DONE**

**Контекст:** все фазы закрыты; §0 аудита фиксировал четыре непроверенных
пункта (нагрузка, pentest, restore на чистую систему, reboot). PHASE 15
закрывает три из них (pentest остаётся вне объёма — см. риски).

**Что сделано:**

- **Задача 55 — reboot-тест X96:** до — enabled/active, NRestarts=0,
  health 200, `user_version=2, tables=15, devices=40, events=37270`,
  ошибок 0; `systemctl reboot` → из Windows опрос: панель сама
  вернулась через ~50 с (systemd boot 34.6s); после — enabled/active,
  NRestarts=0, health 200, **данные бит-в-бит идентичны** (40/37270),
  journal за boot чист. Автозапуск работает без ручного вмешательства.
- **Задача 56 — disaster-recovery дрил на чистую систему:** чистый
  префикс `/tmp/recovery-test` на X96. `install.sh --prefix …
  --unit-dir … --skip-apt --no-enable`: код скопирован (md5
  `26ca64a1… == repo`), venv `--system-site-packages` собран, юнит
  смоделирован (`ExecStart=/tmp/recovery-test/venv/bin/python …`),
  health-шаг прошёл (боевая панель). Затем `recovery.sh --db
  /srv/backup-db/devices_20260930-233341.db --config-tar … --prefix
  … --unit --unit-dir … --no-restart`: code.tar.gz из
  `/var/backups/lan-discovery/20260930-232055` → **SYNTAX OK
  (22 файла)**, БД → **devices=40 events=37184 v2 integrity=ok —
  счётчики точно совпали с бэкапом**, конфиг восстановлен (16
  элементов: settings/secret.key/users…), юнит записан в префиксный
  unit-dir (боевой `/etc/systemd/system` не тронут). Уборка ok,
  боевой сервис active. Восстановление из backup на чистую систему —
  подтверждено end-to-end.
- **Задача 57 — нагрузочный smoke (live, X96):** warmup; 200×GET
  `/api/status` в 20 потоков (43.4s); 100×`/api/status` **во время**
  ручного `POST /api/scan` (scan: 200 за 31.7s); 10 параллельных
  логинов; 50 анонимных `/`. Итог: **356×200 + 9×302, 0×5xx/ERR,
  errors: none** — дедлоков и падений нет; latency p50=3406ms,
  p95=5124ms (20-поточный dev-Werkzeug — известное ограничение,
  гunicorn/reverse-proxy осознанно отложены threat model PHASE 8).
- **Задача 58 — релиз:** git tag **`v1.0.0`** (запушен), §1
  актуализирована (обе платформы на одной версии; таблица 30.09
  сохранена как история), §6 задачи 55–58 DONE, §7 PHASE 15 →
  **DONE — ROADMAP исполнен полностью**.

**Тесты:** unit 54/54 (X96 + OP), live 15/15 (P8), reboot-данные
идентичны, recovery-дрил == бэкапу, нагрузочный smoke без 5xx.

**Остаточные риски (для 1.x/2.0):** пентест не проводился (LAN-only,
admin/1234 по умолчанию — сменить в эксплуатации); dev-Werkzeug под
нагрузкой (p50 > 3s при 20 потоках) — гunicorn-переход отложен;
brute-force `/login` без rate-limit; двойное сканирование LAN (P4,
осознанно); LAN `.234` до физического восстановления кабеля.

### 01.10.2026 — Weather: stale-предупреждения/прогноз + panel_name в шапке + Flask-SocketIO pin — **DONE**

**Жалобы:** на `/weather` висят неактуальные предупреждения, недельный
прогноз тоже; ранее — терминал на OP не работал.

**Диагностика:**

- **Прогноз:** в `weather_forecast` лежали просроченные хвосты
  (28–30.09 при свежих 01–07.10, fetched_at обновлялся каждые 15 мин);
  ридер `ORDER BY forecast_date LIMIT 7` без фильтра отдавал первые
  (старые) 7 строк → на странице даты из прошлого. Причина хвостов —
  `weather-update.py` делал только `INSERT OR REPLACE` без DELETE.
- **Предупреждения:** на X96 стоял урезанный `deploy/weather-update.py`
  (30.09, без фетчеров алертов) → `weather_alerts`/`mchs_alerts`
  замёрзли на 28.09 (писателя в git не было — docs признавали это);
  на OP работал полный легаси-скрипт 894 строки из `/usr/local/sbin`
  (метеoinfo + МЧС), поэтому там данные были свежие.
- **Терминал на OP:** Debian `python3-flask-socketio 5.5.1` падает с
  Flask 3.1: `AttributeError: property 'session' of 'RequestContext'
  object has no setter` в `_handle_event` → connect-хендлер отклонялся
  («One or more namespaces failed to connect»). На X96 стоял pip
  5.6.1 — работал.

**Изменения:**

- `deploy/weather-update.py` — перенесены из легаси `fetch_weather_alert`
  (meteoinfo.ru/informer/meteoalert, POST-код региона из `REGION_CODES`)
  и `fetch_mchs_alert` (37.mchs.gov.ru, regex карточки статьи + тело),
  вызовы после коммита погоды, каждый в своём try/except (сбой
  источника не валит observations/forecast); `DELETE FROM weather_forecast
  WHERE forecast_date < today` — чистка хвостов;
- `modules/weather_routes.py` — ридеры: `weather_forecast` фильтр
  `forecast_date >= сегодня`; `weather_alerts` — свежесть 24 ч
  (`substr(fetched_at,1,16) >= now-24h`); `mchs_alerts` — если в тексте
  нет «до HH:MM D месяца YYYY года», статьи старше 3 дней по
  `published_at` прячутся;
- `tests/unit/test_weather.py` — **9 unit-тестов** (хвосты прогноза,
  LIMIT 7, свежесть/пустота гидромет-алертов, expiry МЧС, старость без
  expiry, свежие показываются, будущий expiry важнее возраста);
- `app.py` — `panel_name()` (settings `panel_name` → fallback
  `socket.gethostname()`), добавлен в `page_data()`;
  `templates/base.html` — суффикс в `<title>` и в `<h1>` шапки;
  `templates/login.html` + `modules/auth.py` — то же на странице входа:
  несколько открытых панелей различимы: «LAN Discovery (x96max)»;
- `requirements.txt` — `Flask-SocketIO>=5.6,<6.0` (фикс Flask 3.1;
  на OP версия уже поднята pip'ом вручную в этой сессии).

**Деплой X96:** бэкапы `*.backup-p16-*` (app/weather_routes/auth/
base/login/weather-update) → pscp → `py_compile` OK → **pytest 63/63**
(54 + 9 новых) → `deploy/weather-update.py` → `/usr/local/sbin/` →
ручной прогон: `WEATHER ALERT OK: нет предупреждений`,
`MCHS ALERT OK: Экстренное предупреждение на 30 сентября по 01 октября
2026 года`, `WEATHER UPDATE OK (прогноз: 7 дн.)` → `panel_name=x96max`
в settings.json → рестарт → active.

**Проверка страницы (X96):** `<title>LAN Discovery (x96max)`, шапка с
суффиксом; даты прогноза **1–7 окт.** (28–30.09 удалены из БД);
«Туман» от 28.09 и МЧС 28–29.09 исчезли; свежий МЧС 30.09–01.10 виден;
`weather_alerts` — свежая строка с `alert=NULL` (не рендерится).

**Остаточные риски:** недоступность meteoinfo >24 ч — блок
гидромет-предупреждений исчезнет (осознанно: пусто лучше устаревшего);
МЧС-статья без разбираемой даты окончания живёт максимум 3 дня;
шаблоны, отрендеренные без `page_data`, не показывают суффикс (guarded
`{% if panel_name %}`); OP ещё предупреждён к деплою этой версии.

### 01.10.2026 - ui: динамическая справка /help (не зашита под X96) - **DONE**

**Проблема (жалоба):** `/help` на Orange Pi описывал X96 Max — плата
«X96 Max (Amlogic S905X)», IP `.243/.244`, restore `:8081@.243`, диск-таблица
(SD-система, пустая eMMC, нет HDD), `ssh root@192.168.3.243`, «системный
раздел SD», клонирование с `mmcblk1/mmcblk2` — на OP всё иначе (eMMC
-система, HDD `/srv`, SD `/mnt/sd`, primary `.235`).

**Изменения:**
- `modules/core_routes.py` — `_help_facts()`: `board_title()`/hostname/OS из
  `about_data()`; primary IPv4 (предпочтение `192.168.*`, только
  `network_physical` — без docker `172.17.*`); порт `web.flask_port`,
  `network.subnet`, координаты+регион из `settings.weather`; таблица
  накопителей из lsblk **с фильтром имён** (`mmcblk*/sd*/vd*/nvme*` — без
  `ram0..N`), роли: системный/данные/пустой; LAN-таблица с маркером
  **«эта панель»** по `primary_ip`; `root_src/root_kind( label)`,
  `sd_dev/emmc_dev` через `find_typed_block`; `help_page()` —
  `dict(page_data()) + hf` (без мутации кэша `page_data`);
- `templates/help.html` — обзор («на базе {board}»), порт, координаты
  Open-Meteo, блок «Основное устройство» (IP-строки из фактов), «Накопители»
  `{% for %}`, LAN-таблица `{% for %}` с маркером, регион погоды,
  «Управление сервером {board}», «системный раздел {root_kind_label}»
  (2 места), curl-координаты из settings, клонирование (root/sd/emmc
  динамически, строки guard'ятся `{% if hf.sd_dev/emmc_dev %}`),
  `ssh root@{primary_ip}`, subnet в nmap/FAQ;
- `tests/unit/test_help.py` — **2 unit-теста**: рендер `/help` (login via
  monkeypatched `load_users` + session) — hf-значения на месте, старый
  X96-хардкод отсутствует (conditional: не для самого X96); структура
  `_help_facts()` (порты, диски с dev/kind/role, ≤1 маркер).

**Деплой (оба узла):** бэкапы `modules/core_routes.py.backup-help*` +
`templates/help.html.backup-help*` → pscp → `py_compile` OK →
**pytest 65/65** (63 + 2) на X96 и OP → рестарт → active;
sha1 `core_routes.py` локаль == X96 == OP.

**Проверка `/help` (probe, оба узла, HTTP 200):**
- X96: «на базе X96 Max, hostname armbian», LAN `.243` + «Другой IP .244»,
  панель/restore `http://192.168.3.243:8080/8081`, диски `mmcblk1 (SD)
  -системный` + `mmcblk2 (eMMC) пустой`, «эта панель» на строке X96,
  `ssh root@192.168.3.243`, «системный раздел SD», клонирование
  `/dev/mmcblk1p2, SD`;
- OP: «на базе Xunlong Orange Pi Plus / Plus 2, hostname orangepiplus»,
  LAN `.235` **без** docker IP, панель/restore `http://192.168.3.235:8080/8081`,
  диски `sda (HDD) /srv данные`, `mmcblk0 (SD) /mnt/sd данные`,
  `mmcblk2 (eMMC) 14.6G системный`, «эта панель» на строке Orange Pi
  (WiFi AP), `ssh root@192.168.3.235`, «системный раздел eMMC»,
  клонирование `/dev/mmcblk2p1, eMMC`.

**Остаточные заметки:** LAN-таблица (7 хостов) — curated-список домашней
сети, IP в ней остаются статичными (это описание инфраструктуры, а не
хоста); на свежей generic-установке без `192.168.*` primary упадёт в
`127.0.0.1`/первый IPv4 — маркер «эта панель» просто не совпадёт.

### 01.10.2026 — backlog после 1.0: репо-hygiene, чистка §2, PHASE 16, чек-лист пентеста — **DONE**

**Репо-hygiene (`d4535c5`):** удалены из git 13 junk-файлов
(`patch_app.py`, `ssh_query.py`, `find_tv.py`, `install_xplore.py`,
`tv_adb.py`, `test_api_auth.py`, `test_status.py`, `test_sys.py`,
`run_update.py`, `ophub-issue-draft.md`, `modules/{inventory,monitor,
recycling}_b64.txt`) + 2 битые путь-копии из рабочей директории
(`templates__help.html`, `tools__make_demo.py`); перед удалением —
grep-ссылок: только исторические упоминания в этом документе, код/CI/
deploy не используются.

**§2 Сводная таблица:** все 24 строки помечены «закрыта» — добиты 10
хвостов по закрытым ранее фазам: веб-терминал/filemanager/сетевые роуты/
сейф/авторизация/CSRF (P0-1…P0-6), изоляция сбоев (P1-7), фоновые задачи
(P1-8), Observability (P1-9 / PHASE 12), Repo hygiene (01.10).

**PHASE 16 (§7):** состав определён — задачи **59–65**: одна ведущая
копия сканера (§8.1), локализация CDN + CSP (§8.5), drift-контроль
repo↔сервер (§8.2), регресс-пентест по чек-листу, gap'ы §2 (UI-выбор
интерфейсов, identity MAC+IP, семверы), харддинг-резидуалы (TLS/non-root),
прятанье eMMC-блоков на X96. Статус: DEFERRED до старта.

**`docs/Пентест.md`:** чек-лист самостоятельной проверки — 9 секций
(периметр, аутентификация, роли, CSRF/XSS/заголовки, инъекции/SSRF/path
traversal, секреты, сеть/DoS, платформа, фиксация результата); ссылки
добавлены в `_Sidebar` и `Home`; `sanitize_docs.py` → **leaks: 0,
exit 0** (16 файлов в публичную копию).

**Сверка:** CI success (`ace2dda`, `d4535c5`); синхронизация X96 —
exact=145, content_diff=0, only_remote=0.

### 01.10.2026 — ui: этап 1 модульной справки — секции /help следят за тумблерами — **DONE**

**Суть:** справка `/help` теперь показывает только те разделы, чьи
модули включены (модульная механика `help_sections()` + `help.md`
уже работала; доделаны статические секции 3–10 и сайдбар).

- `modules/core_routes.py`: `_help_facts()` собирает
  `hf.enabled` — множество `installed and enabled` id из
  `core.module_loader.discover_modules()/module_status()`.
- `templates/help.html`: сайдбар (якоря 4/6/7/10) и секции привязаны
  к модулям: §3 Мониторинг/Валюты/Погода, div `#weather`, §5 диски/
  Wi-Fi, div `#economics` + переработка, div `#multimedia` (IPTV/
  камеры/плеер/radio/радиация), §8 утилиты (nettools…upnp), §10
  sys-users/sys-settings/sys-db (+eMMC при `sys-emmc`); ядро
  (устройства/система/приложения-базовые/игры/API/admin) — без
  условий. Нумерация секций статична (возможны пропуски при
  выключении) — сквозная динамика отдана в этап 2 (перенос текста в
  `help.md` + дописать 14 недостающих help.md у sys-*/curr-*) —
  выполняется только по явному подтверждению.
- `tests/unit/test_help.py` +2: наличие секции/якоря по умолчанию;
  мок `core.module_loader.module_status` (weather → выкл) → в /help
  нет `id="weather"`, `href="#weather"`, «Радиационный мониторинг»,
  ядро не задето. Итого **67/67** на X96 и OP.
- Live-верификация на X96: `wifianalyzer` off → секции «Сканер Wi-Fi
  сетей»/«📶 Wi-Fi» исчезли, ядро осталось; on → вернулись;
  `modules.json` восстановлен (`enabled: true`).
- Деплой: бэкапы `*.backup-modhelp-*`, sha1 трёх файлов
  локаль==X96==OP, probe `/help` 200 на обоих узлах (факты X96 —
  X96 Max/.243/SD-система; OP — Orange Pi/.235/eMMC-система).

### 01.10.2026 — ui: этап 2 модульной справки — весь контент /help живёт в help.md — **DONE**

- 14 недостающих `help.md` создано (`sys-users`, `sys-settings`, `sys-db`,
  `sys-emmc`, `sys-iptv`, `sys-network`, `sys-power`, `sys-board`,
  `sys-clone`, `curr-fiat/precious/industrial/crypto/recycling`) +
  `"help": true` в 14 `module.json` — `help_sections()` требует и флаг,
  и файл; в 5 существующих (`monitoring`, `weather`, `notes`, `passwords`,
  `radio`) дописан переносимый текст из help.html (UV/радиация/AQI/пульс
  сервера, Markdown-редактор, генератор паролей, ручное добавление
  станций). Итого `help=true` + `help.md` у **всех 33 модулей**.
  Утверждения старого help.html про «HTML5-плеер» и «загрузку файлов»
  проверены по коду — не подтвердились (mpv-воспроизведение, upload
  нет), в help.md не переносились.
- `templates/help.html`: вырезаны секции 4/6/7 целиком и модульные
  подсекции §3/5/8/10 — шаблон остался только с ядром; нумерация
  1–11 (было 1–14), сайдбар и внутренние ссылки («разделах 8 и 9» →
  «5 и 6», «раздел 12» → «9») перенумерованы; 713 → 400 строк;
  модульные секции рендерятся `help_sections` как `mod-<id>`.
- `requirements.txt`: + `markdown>=3.5,<4.0` — рендер help.md
  (на OP отсутствовал в venv → секции падали в fallback `<pre>`;
  установлен, `pip install -r requirements.txt` на обоих узлах).
- Тесты обновлены под `mod-*`; **67/67** на X96 и OP. Live-verify:
  33 секции = 33 якоря сайдбара, markdown-рендер (`<p>`/`<ul>`,
  не `<pre>`), тумблеры: `curr-recycling` (X96) и `sys-board` (OP) —
  off → секция и якорь исчезли, on → вернулись. sha1 четырёх файлов
  локаль==X96==OP; `/help` 200 на обоих (факты свои).

### 01.10.2026 — fix: OP — DLNA (SSDP через ufw) и Samba-диагностика — **DONE**

- Симптом: панель OP не видит DLNA-сервер X96 (`X96 Max DLNA`), хотя
  minidlna X96 жив (`:8200`, HTTP 200 с OP), а скан с X96 видит OP.
- Диагностика по шагам: M-SEARCH от OP доходит до X96 (raw-снифф
  `AF_INET`), minidlna X96 получает и отвечает (strace
  `sendto = 351`), ответ приходит на интерфейс `wlan1` OP
  (`AF_PACKET`-снифф) и **дропается ufw** (INPUT policy DROP): unicast-
  ответ на multicast M-SEARCH не матчится conntrack'ом как
  ESTABLISHED → NEW → DROP — UDP-правил в ufw не было (вся асимметрия:
  OP→X96 работал, X96→OP — нет).
- Фикс: два ufw-правила из LAN — `1900/udp ALLOW 192.168.3.0/24`
  (входящий M-SEARCH) и `src 1900/udp ALLOW 192.168.3.0/24` (ответы
  SSDP); бэкап правил — `/root/ufw-backup-20261001-103538.txt`. После:
  probe получает `REPLY from 192.168.3.243`, скан панели OP находит
  «X96 Max DLNA».
- Samba («не видно файлов»): smbd/nmbd active, порты 445/139 слушают,
  ufw 445/139 allow LAN; все шары (`downloads`, `media`, `share`) —
  `valid users = kot`, гостевые подключения (в логах `user nobody`)
  получают `NT_STATUS_ACCESS_DENIED` (проверено `smbclient -N -c ls`).
  Файлы на диске есть, права kot-читаемы, smb-пользователь `kot` есть
  (пароль задан 29.09), активная сессия ThinkPad `.236` как `kot`
  на `downloads` работает. `/srv/share` существует (ложный след —
  `head` обрезал вывод `ls`). Включение гостевого доступа
  (`guest ok = yes`) — решение администратора, не применялось.

### 01.10.2026 — core: гостевой доступ Samba + тумблер в панели — **DONE**

- По решению администратора: в LAN можно заходить гостем, с
  возможностью отключения из веб-панели. Реализовано `core/
  samba_guest.py` (9 unit-тестов): `transform()` — чистая идемпотентная
  правка smb.conf: в файловых шарах (секции с `path`, кроме
  `printers`/`print$`/`homes`/`global`) `guest ok = yes` + `valid
  users` временно комментируется префиксом `# lan-discovery guest: `
  (восстанавливается при выключении, отступ сохраняется), в `[global]`
  при отсутствии прописывается `map to guest = bad user`. `apply()`:
  бэкап `smb.conf.backup-<дата>` → `testparm -s` на tmp-файле →
  атомарная подмена → `smbcontrol all reload-config` (сессии не
  обрываются; fallback `systemctl reload smbd`). Состояние читается
  из самого smb.conf (отдельный флаг не хранится).
- API: `GET /api/samba/guest` (login) и `POST /api/samba/guest`
  `{"enabled": bool}` (admin, CSRF) в `modules/system_routes.py`;
  каталог роутов обновлён (163 → **165**, system-секция 24 → 26,
  admin-роуты 40 → 41; счётчики в README/API/Home/Архитектура/
  Безопасность).
- UI: строка «Гостевой доступ Samba» в блоке платы на /system
  (`modules/sys-board/block.html`; кнопка только для `admin`),
  JS `renderSambaGuest`/`loadSambaGuestState`/`toggleSambaGuest` в
  `base.html` (подтверждение confirm, JSON+CSRF).
- Документация: `docs/Конфигурация.md` — раздел «Samba: шары и
  гостевой доступ» (что делает включение, бэкап/testparm, ограничения,
  порт 445: ufw на OP, нет firewall на X96); `docs/Безопасность.md` —
  пункт в «Известных ограничениях» (гость = запись/чтение шар без
  пароля от владельца через `force user`); `docs/API.md` — 2 строки;
  `sys-board/help.md` и `templates/help.html` — упоминание
  переключателя.
- Тесты: **76/76** на X96 и OP (67 + 9). Live-verify на обоих
  (`/tmp/verify_smb.py`): аноним GET → 302, аноним POST без CSRF →
  400, baseline OFF (3 шары restricted), невалидные тела → 400,
  цикл ON → все `guest ok`/`restricted=false` → OFF → `valid users`
  восстановлены → ON (финал **включён**) + идемпотентный повтор
  (`changed=false`), строка и кнопка на /system. Гостевой доступ
  проверен `smbclient -N`: OP → `//192.168.3.243/share` (X96) и
  локально `//127.0.0.1/downloads` — файлы видны. sha1 трёх файлов
  локаль==X96==OP; `/help` 200 на обоих; бэкапы
  `*.backup-smb-*` + `smb.conf.backup-*` созданы.

### 01.10.2026 — chore: репозиторий 1.1 + тестовая система X96 — **DONE (старт 1.1)**

- По решению владельца начата **версия 1.1** (промпт «UI/UX Redesign +
  Capabilities/Modules/Roles»: appliance-модель Hardware →
  Capabilities → Modules → Roles → UX, STEP 1–12, DoD в промпте).
- Оценка объёма: STEP 1–12 ≈ **120–180 ч / 10–14 сессий**, сложность 7/10;
  в 1.1 **не входят** (→1.2/1.3): сетевые роли (Router/Firewall/DHCP/DNS),
  транзакционный netconf (Prepare/Apply/Verify/Commit/Rollback), recovery-AP,
  подписи модулей. Противоречия промпт↔код зафиксированы в оценке
  (capabilities — только достоверные; статусы модулей требуют расширения
  module.json; topology-страниц в 1.1 нет; alerts = вьюха поверх events;
  роли = конфиг-слой поверх modules).
- Создан приватный **`Lan-discovery-1.1`**
  (https://github.com/kotmartovskiy/Lan-discovery-1.1), снапшот `main`
  `ea4da77` с полной историей; локальный `origin` → новый репо,
  `origin-1.0` → `Lan-discovery-ARM` (заморожен, OP на 1.0).
- **Тестовая система 1.1 — X96 Max** (`.243`); **Orange Pi не трогаем**
  (остаётся рабочим инструментом на 1.0 — деплои 1.1 на OP запрещены).
  AGENTS.md обновлён (репозитории + рабочее окружение).
- Следующий шаг: STEP 1 — аудит `UI_UX_AUDIT.md` (страницы/навигация/
  API каждой страницы/проблемы/таблица Keep|Redesign|Backend change).

### 01.10.2026 — docs: STEP 1 аудит UI/UX — **DONE**

- Создан **`UI_UX_AUDIT.md`** (корень репо) по промпту п.3: только реальный
  код (28 шаблонов / 10 383 стр., 165 роутов, 33 модуля), read-only
  разведка + 3 отчёта.
- Ключевые находки: 4 параллельных дизайн-системы, `static/style.css`
  (442 стр.) **не подключён** (копия инлайнена в `base.html`, монолит
  2 124 стр.); 0 токенов, 60 инлайн-hex-фонов; двойной poll `/api/status`
  3 с (второй на 13/14 страниц впустую); `/apps` — 11 iframe статически +
  ~60–70 запросов/мин; нет `<meta viewport>` в base.html (mobile сломан);
  доступность ~0 (aria/role/tabindex/alt = 0); 27 нативных alert/confirm;
  33 `"--"` без состояний; сироты: `/history` без ссылок,
  `inventory_device.html` — мёртвая заглушка; **утеряны кнопки** backup/
  backup-test/emmc-restore (JS есть, кнопок нет); 14 API недостижимы из UI.
- Таблица Keep|Redesign|Backend change (24 строки): backend пригоден, нужны
  только аддитивные `GET /api/dashboard`, `GET /api/capabilities`, слой
  roles, расширение схемы module.json (version/source/ports/permissions/
  hardware) для статусов Available/Requires hardware/Incompatible.
- Распределение: в 1.1 — STEP 1-12 (фундамент: design system, IA,
  dashboard, capabilities v1, modules v1, roles-архитектура, responsive,
  a11y, cleanup); в 1.2 — topology/interfaces/services + сетевые роли;
  в 1.3 — транзакционный netconf + recovery.
- Следующий шаг: STEP 2 — design system (`static/style.css` как источник
  истины: токены, примитивы, semantic states, demo/unavailable) + уборка
  инлайн-CSS в `base.html`; деплой только на X96.

### 01.10.2026 — ui: STEP 2 design system — **DONE**

- `static/style.css` (442 → 607 строк) стал источником истины для страниц
  base.html: 1) **design tokens** `:root` (цвета поверхностей/текста/
  семантики, радиусы, значения кнопок), 2) **primitives** (`.btn` +
  primary/danger/ghost/sm, `.status-warn/-critical/-unknown`, `.na`,
  `.empty-state`, `.spinner`, `.modal/.toast`) — все имена свободны
  (`class="btn"` не использовался ни в одном шаблоне), 3) **legacy-блок** —
  466 строк, вырезанных из inline `<style>` base.html без изменений.
- `base.html` (2124 → 1656 строк): inline `<style>` →
  `<link rel="stylesheet" href="/static/style.css?v=20261001">` (cache-bust);
  порядок каскада сохранён — primitives ПЕРЕД legacy (при равной
  специфичности побеждает legacy = визуальный паритет), собственные `<style>`
  страниц (в body) перекрывают файл, как перекрывали base-инлайн; Jinja-head
  чист (единственный block `content` — в body).
- Фикс бага: в `.system-status` была **висячая `}`** (старая строка 121) —
  правила `font-size/color/white-space/width` терялись браузером; блок
  склеен. **Видимое изменение (ручная проверка):** текст нижнего
  статус-бара 16px → 14px, цвет `#b8c4d1` (авторский замысел).
- Фикс: убран дубль `<body>` (старые строки 504/506, P-2 из аудита).
- Проверки на X96: `check_step2.py` — **15/15 PASS** (логин → ссылка на `/`
  и `/system`, head без `<style>`, один `<body>`, `:root`/primitives,
  баланс скобок 115/115, фикс `.system-status`), pytest **76 passed**,
  sync **exact=161 / content_diff=0 / only_remote=0**. Бэкапы на сервере:
  `base.html.backup-20261001-125933`, `style.css.backup-20261001-125933`.
- Следующий шаг: STEP 3 — информационная архитектура: доменные группы
  навигации (§8 промпта), вывод `/history` из «сирот», порядок/группировка
  табов; затем STEP 4 — dashboard + `GET /api/dashboard`.

### 01.10.2026 — ui: STEP 3 информационная архитектура навигации — **DONE**

- **Доменные группы в шапке** (§8): `Устройства | Мониторинг | Приложения |
  Система | Помощь` — микро-labelы над рядом ссылок (`.nav-group*` в
  style.css, flex, wrap; блок ПОСЛЕ legacy → перекрывает `.tabs a`
  margin). Группы и порядок — `NAV_GROUP_ORDER` в `core/module_loader.py`.
- `CORE_NAV`: +`group` каждому пункту; **+«История» `/history`** (order 35,
  группа «Мониторинг» — сирота из аудита получила вход); **+«Модули»**
  (order 85, группа «Система», флаг `admin`) — ручной admin-`<a>` в
  base.html убран (фильтрация роли в `nav_groups(admin=...)`).
- `nav_groups(admin)` — новая функция;5 модульных `module.json` получили
  `tab.group` (inventory→Устройства; monitoring/currencies/weather→
  Мониторинг; torrent→Приложения). Новые модули без `group` → «Прочее»
  в конец. Всего: admin 12 пунктов / гость 11.
- context_processor `modules/module_manager.py` отдаёт `nav_groups`
  (роль берётся ленивым импортом `modules.auth.get_current_user`).
- **Инцидент при проверке**: 500 на рендере — ключ словаря `items`
  конфликтует с методом `dict.items` в Jinja (lookup атрибута первым) →
  переименован в `entries`; параллельно падали3 теста test_help (они
  рендерят base). Поймано live-check'ом и pytest до коммита.
- Проверки на X96: `check_step3.py` — **15/15 PASS** (порядок групп, 12
  ссылок admin, активные вкладки на /, /history, /modules, CSS-селекторы),
  pytest **76 passed**, sync **exact=161 / content_diff=0**. Бэкапы:
  9 файлов `*-s3-20261001-131451` + пере-upload `*-s3b-20261001-131830`.
- Следующий шаг: STEP 4 — dashboard (единый поллер + `GET /api/dashboard`).

### 01.10.2026 — core: STEP 4 dashboard-агрегат + единый поллер — **DONE**

- **`GET /api/dashboard`** (`modules/system_routes.py`, рядом с api_status):
  агрегат `system` (кэш /api/status 2с) + `health` (кэш30с) + `devices`
  (count/online из devices.db) + `events` (последние10, `core/events`) +
  `alerts` + `internet` (`check_internet_cached`) + `checked_at`. Один
  запрос вместо `/api/status` ×2 + `/api/system/health`; **старые endpoints
  не удалены** (регресс-тест).
- **`core/dashboard.py`** — `merge_alerts()`: alerts = read-only вьюха
  поверх health-warnings + событий severity warning/critical (без новой
  таблицы БД, решение по промпту §6); unit-тесты на level/source/limit.
- **base.html**: `updateSystemStatus` + `updateOrangePiStatus` +
  `checkHealth` → три render-функции (`renderSystemStatus`/`renderBoardStatus`/
  `renderHealth`) + один `updateDashboard()` (fetch `/api/dashboard` каждые
  3с, плюс `renderInternet` — интернет-индикатор в шапке теперь live,
  `id="internet-status"`); убраны2 интервала3с + health-интервал60с →
  **−2/3 запросов статус-слоя** (polling-карта из аудита P-3 закрыта).
  `await updateOrangePiStatus()` после service-действий → `updateDashboard()`.
- Проверки на X96: `check_step4.py` — **29/29 PASS** (агрегат:8 ключей,
  devices 40/24, events10, alerts6, internet=true; регресс /api/status и
  /api/system/health; на / нет старых вызовов и '/api/status',
  render-функции на месте; /system с pi-виджетами рендерится), pytest
  **81 passed** (76+5), sync **exact=161 / content_diff=0** (2 новых файла
  — в git после коммита). Бэкапы `*-s4-20261001-133303`.
- Примечание: старт сервиса ~4с (скан+схема) — после рестарта ждать
  привязки порта (для следующих проверок sleep ≥5с).
- Следующий шаг: STEP 5 — devices list/detail (6 ключевых колонок,
  semantic states, drawer устройства).

### 01.10.2026 — ui: STEP 5 devices list/detail — **DONE**

- **`templates/devices.html` — 6 ключевых колонок** вместо 10: Статус · Тип ·
  Устройство (имя+ссылка+NEW) · IP/Hostname (одна колонка) · Сеть (MAC+вендор)
  · Активность (last_seen + пропуски/появлений). Все данные сохранены
  (keep-list), гостевые ограничения без изменений (без IP/Hostname/MAC;
  в «Сети» для гостя только вендор); кнопка скана переведена с inline-стиля
  на `.btn .btn-primary .btn-sm`; пустые значения → `.na` вместо голого `-`.
- **Fix**: `devices_routes.index` передавал в шаблон 11 полей, а `d[11]`
  (device_type) отсутствовал — колонка «Тип» была всегда пустой; в кортеж
  добавлен `d[11]` (9 устройств на X96 с типом — иконки рендерятся).
- **`templates/device.html` — три уровня** (вместо плоской таблицы):
  1) **Устройство** — semantic-статус, hostname/MAC/вендор/тип/первое/
  последнее/статистика через `.info-row`/`.info-label`, пустые → `.na`;
  2) **Оборудование и сервисы** (НОВОЕ) — `modules.inventory.get_inventory(ip)`:
  ОС/модель/CPU/память (info-rows), таблица открытых портов (порт/протокол/
  сервис из nmap `-O -sV`), «Инвентаризация от» + ссылка `/inventory`;
  без данных → `.empty-state` с ссылкой на инвентаризацию;
  3) **История** — события + severity (`critical`→`.status-critical`,
  `warning`→`.status-warn`), пусто → `.empty-state`.
- **Backend (минимум)**: `device(ip)` — `severity` в SELECT событий +
  `get_inventory(ip) or {}` в контекст (try/except, ошибки не роняют
  страницу); переименование/тип/dismiss-new/сортировка не тронуты.
- Проверки на X96: `check_step5.py` — **32/32 PASS** (6 новых колонок +
  отсутствие 7 старых, `.btn` без inline, .na на устройстве 192.168.3.7,
  3 уровня detail, регресс /api/status, /api/dashboard, /inventory),
  pytest **81 passed**, sync **exact=163 / content_diff=0 / only_remote=0**.
  Бэкапы `*-backup-s5-20261001-134753`.
- Следующий шаг: STEP 6 — monitoring/утилиты (семантические статусы
  services-страницы, единый язык состояний).

### 01.10.2026 — ui: STEP 6 monitoring — semantic states, единый язык — **DONE**

- **`monitoring.html`**: 12 hex-тернарников порогов (`color:#65d46e/#ffb84d/
  #ff6666`, 60/80%, температура 60/75) → Jinja-макрос `lvl(v, warn, crit)`
  → классы `.status-ok/.status-warn/.status-critical`; «Недоступен» (нет
  netdata) → **`.na` (unknown-состояние, P-10 закрыт)**; «Доступен» →
  `.status-ok`; пустая секция Netdata → `.empty-state`; локальные
  `.bar-ok/warn/crit`, `.badge-*` — hex → `var(--ok/--warn/--critical/--link)`.
  Декоративные стили секций (h4, фоны аккордеона) не тронуты.
- **`history.html`**: severity inline-hex (`#ef5350/#f59e0b/#9aa0a6`) →
  `.status-critical/.status-warn/.status-info` (новый примитив).
- **`inventory.html`**: online-IP `style="color:#65d46e"` → `.status-ok`;
  бейдж NETDATA hex → `var(--ok)`.
- **`static/style.css`**: добавлен `.status-info { color: var(--info) }`
  (примитив semantic states); `.status-ok`/`.status-error` переведены с
  хардкода hex на `var(--ok)/var(--error)` (единый источник цветов).
- **`base.html`**: 5 JS-индикаторов (ping «доступен/недоступен» ×3,
  `renderHealth` OK/проблемы) hex/`#f85149` → `var(--ok)/var(--critical)`;
  cache-buster CSS `?v=20261001b` (браузерный кеш старого style.css).
- Проверки на X96: `check_step6.py` — **23/23 PASS** (нет hex-тернарников,
  классы lvl/`.na`/empty-state в рендере, history на классах, inventory
  status-ok, CSS-примитивы + cache-buster, регресс `/`, `/system` blocks,
  `/api/dashboard`); pytest **81 passed**, sync **exact=163 / content_diff=0**.
  Бэкапы 5 файлов `*-backup-s6-20261001-140215`.
- Остаток на polish: inline-hex в app-страницах (`dlna.html` JS-ошибки,
  `base_app.html` палитра) — не пороги состояний, вне скоупа STEP 6;
  в `events` нет severity=critical (info/warning) — ветка покрыта тестом
  условно.
- Следующий шаг: STEP 7 — capabilities (core/capabilities.py, достоверные
  данные).

### 01.10.2026 — core: STEP 7 capabilities (достоверные + reliability) — **DONE**

- **`core/capabilities.py`** (новый, read-only, без зависимостей от app —
  как hardware.py): `collect()` (кэш 30 с) → `board` (модель из
  /proc/device-tree/model = **measured**; fallback hostname = **unverified**),
  `storage` (emmc/sd/hdd из `core.hardware` — present+detected /
  absent+detected / unknown+unverified при ошибке), `thermal` (зона+temp:
  measured при чтении, absent если зон нет, unknown если зона нечитаема),
  `tools` (10 бинарей PATH, кэш 300 с), `checked_at`. Формат записи:
  `{state: present|absent|unknown, reliability: measured|detected|unverified,
  value?}` — **unknown всегда ⇒ unverified** (ничего не угадывается).
- **`TOOL_PROBES`** — единый источник пробов: `app.py` теперь импортирует
  (`as CAPABILITY_PROBES`), дублирующий кортеж удалён; `/api/health`
  (bool-формат P1-11) не изменён.
- **`GET /api/capabilities`** + **страница `GET /capabilities`**
  (`modules/system_routes.py`, login_required): уровни Плата / Накопители /
  Терморегуляция / Внешние инструменты; semantic-состояния
  (status-ok / .na / status-unknown) + объяснение reliability + ссылка на
  JSON.
- **Навигация**: `CORE_NAV` += «Возможности» `/capabilities` (group
  Система, order 82 — сразу после «Система»).
- **`tests/unit/test_capabilities.py`** (+6): shape/reliability-валидация,
  unknown⇒unverified, probe_tools покрытие, `_cap`, API-shape, рендер
  страницы, `app.CAPABILITY_PROBES is TOOL_PROBES` (единый источник).
- Проверки на X96: `check_step7.py` — **26/26 PASS** (API-shape,
  валидность 10 tools + storage/thermal, страница/4 уровня/semantic,
  nav-ссылка, регресс `/`, `/system`, `/api/dashboard`, `/api/health`),
  pytest **87 passed** (81+6), sync **exact=163 / content_diff=0**
  (3 новых файла → в git). Бэкапы `*-backup-s7-20261001-141819`.
- Примечание: semantic-ветки данныхозависимы — на X96 `status-unknown`
  не проявился (нет unknown-состояний), чек/тест смягчены на
  «status-ok + (na|unknown)».
- Следующий шаг: STEP 8 — modules v1 (расширение module.json: version,
  source, ports, permissions, hardware, services + вычисляемые статусы).

### 01.10.2026 — core: STEP 8 modules v1 (схема + вычисляемые статусы) — **DONE**

- **Схема `module.json` расширена** (docstring module_loader обновлён):
  новые поля `version`, `source`, `permissions`, `hardware`
  (`{"arch": [...], "tools": [...], "storage": [...]}`); `ports`/`services`
  уже были в `deps` — дублей не заводим. Старые манифесты без полей
  работают как раньше, `version` в UI → **«Unknown»** (33 builtin —
  версии не ведутся, честный Unknown).
- **Вычисляемые статусы** (`core/module_loader.py`):
  `compute_status(m, entry, ctx, missing_pkgs)` — чистая функция,
  приоритет **incompatible** (hardware.arch против
  capabilities.board.arch) → **requires-hardware** (tools/storage из
  `core/capabilities`: не present) → **requires-dependency** (отсутствующие
  apt-пакеты — один `dpkg-query`-batch на все модули, кэш 60 с; не
  смогли проверить → не считаем отсутствующей + `deps.dirs` по
  `os.path.isdir`) → **error** (last.ok == False) → **disabled** →
  **active** → **available**; исключение/битый манифест → **unknown**
  (8 статусов из 9 промптовых; «installed» — подмножество
  active/disabled в нашей модели enabled/installed). `services` из deps
  НЕ проверяются (systemctl-per-unit дорого; решено отложить).
- **`modules_with_status()`** — сборка строк для /modules одним
  dpkg-batch + `status_context()` (arch+capabilities, кэш 30 с);
  `modules_page` использует её (state/last сохранены).
- **UI `modules.html`**: три старых бейджа (не установлен/включен/
  выключен) → один вычисляемый semantic-бейдж (badge-on/off/none +
  новый `.badge-warn` на токенах для требований); version-бейдж
  (vX / **Unknown**); hardware-требования и permissions бейджами в
  `mod-deps`; `source` мелким под описанием. Каталог/toggle/install/лог
  не тронуты.
- **`tests/unit/test_module_status.py`** (+10): все ветки приоритета,
  priority-тест (incompatible > error), shape `modules_with_status`
  (статусы ⊂ MODULE_STATUSES), рендер /modules (бейджи + Unknown).
- Проверки на X96: `check_step8.py` — **15/15 PASS** (бейджи, убран
  старый «включен», badge-warn, Unknown, toggle-кнопки/лог, регресс
  `/`, `/system`, STEP 4/7-страницы и API, nav), pytest **97 passed**
  (87+10), sync **exact=166 / content_diff=0** (1 новый файл → в git).
  Бэкапы `*-backup-s8-20261001-143600`.
- Следующий шаг: STEP 9 — roles-слой (конфиг-профили поверх modules,
  `GET/POST /api/roles*`, проверка hardware-требований).

### 01.10.2026 — core: STEP 9 roles layer (профили модулей) — **DONE**

- **`core/roles.py`** — конфиг-слой поверх modules (module_loader не менялся):
  `PROFILES` (default «Полный» = все модули, media «Медиацентр»,
  network «Сеть и наблюдение»), `ALWAYS_ON` (sys-board/network/power/
  settings/users/db — база панели, роль их не выключает, в UI ★),
  состояние активной роли — `/etc/lan-discovery/roles.json` (создаётся
  при первом apply, дефолт default).
- **`apply_role(rid)`**: compat-check через `compute_status` из STEP 8 —
  incompatible/requires-hardware → **skipped** (не включаются), не
  установленные не трогаются; модули роли → enabled, остальные →
  disabled (кроме ALWAYS_ON); активная роль сохраняется. Возвращает
  `{ok, active, enabled, disabled, skipped}`.
- **`roles_overview()`**: все профили с модулями роли + ALWAYS_ON
  (флаг `always_on`), статусы из словаря STEP 8 и текущий enabled —
  ровно то, что применит apply.
- **Роуты** (`modules/module_manager.py`): `GET /roles` (admin,
  страница с карточками профилей/бейджами/кнопками), `POST
  /roles/<rid>/apply` (admin, redirect ?ok/?err), `GET /api/roles`
  (login, JSON), `POST /api/roles/<rid>/apply` (admin, JSON).
- **Навигация**: CORE_NAV += «Роли» `/roles` order 84 группа Система
  (admin: true) — между Возможности(82) и Модули(85).
- **UI `templates/roles.html`**: карточки профилей (активная —
  зелёная рамка + ● активна), модули — semantic-бейджи по статусу
  (active/disabled/ошибки/требования/available), ★ у всегда-включённых,
  «Применить/Переприменить» с confirm (переприменить = восстановление
  после ручных правок на /modules).
- **`tests/unit/test_roles.py`** (+12): профили ссылаются на реальные
  id, ALWAYS_ON защищён во всех профилях, fallback активной роли,
  apply (вкл/выкл/skip неустановленных) и skip требуемого железа,
  overview-shape (статусы ⊂ MODULE_STATUSES, ALWAYS_ON в каждом
  профиле), API/page, admin-only навигация. POST-тесты — с
  отключённым CSRF по паттерну test_security.
- Проверки на X96: `check_step9.py` — **20/20 PASS** (страница,
  API-shape, всегда-включённые, err-редирект, nav, регресс STEP 4/7/8),
  pytest **109 passed** (97+12), sync **exact=167 / content_diff=0**
  (3 новых файла → в git). Бэкапы `*-backup-s9-20261001-144910`
  (roles.py/roles.html — новые, без бэкапа). Состояние X96: активная
  роль default, modules.json не менялся.
- Следующий шаг: STEP 10 — responsive (viewport + приоритетные страницы,
  §12 «дельта»: viewport, таблицы→scroll, мобильные гриды).

### 01.10.2026 — ui: STEP 10 responsive (viewport + приоритетные страницы) — **DONE**

- **`base.html`**: добавлен `<meta name="viewport" content="width=device-width,
  initial-scale=1">` — главный пробел 1.1 (mobile был сломан: панель
  рендерилась «весь десктоп»; login/app-шаблоны viewport уже имели).
  Cache-buster CSS → `?v=20261001c`.
- **`static/style.css`** — секция «5. Responsive (STEP 10)»:
  глобальный примитив `.table-wrap { overflow-x: auto }` (горизонтальный
  скролл широких таблиц вместо обрезки/вылезания), `input[type=text]
  max-width:100%` (дефолт width:350px не вылезал на 320–375px), медиа
  ≤420px — `.mod-grid`/`.role-grid` → 1 колонка (minmax(340px) держал
  минимум 340px), медиа ≤700px — `.system-status-bar` (нижняя строка
  метрик, nowrap) скроллится, `.device` на всю ширину (td:first-child
  220px не съедал колонку).
- **Приоритетные страницы — обёртки `.table-wrap`**: `devices.html`
  (6 колонок), `history.html`, `inventory.html` (таблица портов),
  `device.html` (порты + история). Существующие адаптивы не тронуты:
  header wrap ≤750px, system-grid 2/1 колонки (1100/700px), nav
  flex-wrap, help sidebar ≤900px, grid-карточки modules/roles
  auto-fill — всё уже работало (legacy + STEP 3/8/9).
- **`tests/unit/test_responsive.py`** (+6): viewport в рендере `/`,
  login-шаблон, table-wrap на `/` и `/history`, responsive-классы на
  /modules и /roles, правила style.css, cache-buster.
- Проверки на X96: `check_step10.py` — **24/24 PASS** (viewport на
  /, /system, /modules, /roles, /capabilities, /apps; table-wrap;
  style.css-правила по URL с бастером; регресс STEP 4/7/9), pytest
  **115 passed** (109+6), sync **exact=170 / content_diff=0** (1 новый
  файл → в git). Бэкапы `*-backup-s10-*` (6 файлов).
- Следующий шаг: STEP 11 — a11y (доступность: focus/aria/контраст
  по семантике, SKIP-link, лейблы форм).

### 01.10.2026 — ui: STEP 11 a11y (базовая доступность) — **DONE**

- **Семантика P-7** (было: main/header = 0, aria = 0, label for 2/48,
  focus-visible 0, outline:none ×8): `base.html` — `<header class="header">`
  и `<nav class="tabs" aria-label="Разделы панели">` вместо div,
  `<main id="main" tabindex="-1">` вокруг block content (до этого контент
  лежал голым в body), **skip-link** «Перейти к содержимому» первым в
  body (`#main`, скрыт до фокуса), декоративный SVG-логотип в h1 —
  `aria-hidden="true"`.
- **Клавиатура**: глобальный `:focus-visible` (outline 2px, offset 2px,
  цвет `var(--link)`) для a/button/input/select/textarea/[tabindex] —
  единый видимый фокус поверх legacy `outline:none`.
- **`login.html`**: `<label for>` для username/password (id login-*),
  error-блок — `role="alert"` (скринридер сообщает ошибку входа),
  убран `outline:none` с полей (фокус = border-color, теперь оба сигнала).
- **Таблицы приоритетных страниц** — `scope="col"` у всех `<th>`:
  devices (6), history (6), device (5), inventory (4).
- **`tests/unit/test_a11y.py`** (+4): семантика `/` (skip/main/nav/header
  + порядок skip-first), scope-колонки `/` и `/history` без голых `<th>`,
  правила style.css, разметка login (label for/alert/без outline:none).
- Проверки на X96: `check_step11.py` — **25/25 PASS** (метки/login-alert
  по POST с неверным паролем, семантика, scope, focus-правила, регресс
  STEP 4/7/8/9/10), pytest **119 passed** (115+4), sync **exact=171 /
  content_diff=0**. Бэкапы `*-backup-s11-*` (7 файлов). Cache-buster
  → `?v=20261001d`.
- Не в скоупе (1.2): aria-live для поллеров (шум каждые 3 с — осознанно
  не делаем), полный lighthouse-аудит app-страниц/iframe.
- Следующий шаг: STEP 12 — cleanup (удалить system_full.html, подключить
  style.css вместо локальных дублей, мёртвый JS), финал 1.1.

### 01.10.2026 — ui: STEP 12 cleanup (финал 1.1) — **DONE**

- **Удалён мусор**: `system_full.html` (тень app-страницы, в .gitignore
  ещё до 1.1, на X96 отсутствовал — убран из локального дерева);
  мёртвый CSS `.app-btn` (17 строк, ни одного использования) из
  `base_app.html`; `templates/inventory_device.html` (5-строчная заглушка
  без JS/ссылок) — `git rm`.
- **P-12 — возвращены утерянные кнопки бэкапов** в `modules/sys-emmc/
  block.html` (JS-обработчики жили в base.html, кнопок в шаблоне не было):
  «Создать backup сейчас» (`btn-backup-create` → POST /system/backup),
  «Проверить backup» (`btn-backup-test` → POST /system/backup-test),
  «Восстановить из backup» (`btn-emmc-restore` → POST /system/emmc-restore)
  — блок под `current_user.role == 'admin'`; все три роута `admin_required`
  + CSRF покрыт fetch-patch. Кнопки БД-бэкапов `sys-db` не тронуты (на месте).
- **Заглушка inventory → redirect**: `GET /inventory/device/<ip>` рендерил
  `inventory_device.html` → теперь `redirect("/device/" + ip)` (эндпоинт
  сохранён для внешних ссылок). `redirect` уже был импортирован.
- **Help/API → docs**: ручной каталог из 21 строки в справке (секция 8,
  id="api" и TOC сохранены) заменён ссылкой на `docs/API.md` (репозиторий
  + публичный Lan-discovery-docs) + опорные эндпоинты и CSRF-заметка;
  каталог вручную расходился с кодом — источник истины один.
- **`tests/unit/test_cleanup.py`** (+6): файлы мусора удалены, `.app-btn`
  нет, кнопки/JS/guard в sys-emmc-блоке, 302-redirect inventory_device,
  сегмент секции help (без help-table, с docs/API.md), регресс /inventory.
- Проверки на X96: `check_step12.py` — **17/17 PASS** (кнопки в /system
  с admin-сессией, 302 /inventory/device → /device/<ip>, help-сегмент,
  регресс STEP 4/5/7/8/9/10), pytest **125 passed** (119+6), sync
  **exact=171 / content_diff=0**. Бэкапы `*-backup-s12-*` (5 файлов,
  включая копию удаляемого inventory_device.html).
- Ловушки чека: строка `/api/clone/start` живёт в JS `base.html:777`
  (наследуется всеми страницами) — проверять нужно **сегмент секции**
  help, а не весь HTML; `/inventory/device` тестить нужно opener-ом
  **с cookies** (иначе аноним → 302 /login вместо 302 /device).
- Не в скоупе 1.1 (→1.2): поэтапная замена ~2193 строк инлайн-CSS
  на style.css (STEP 2 подключил слой, legacy подчищается точечно).
- **Версия 1.1 (STEP 1–12) завершена**: audit → design → IA → dashboard →
  devices → monitoring → capabilities → modules → roles → responsive →
  a11y → cleanup. Дальше — деплой 1.2 по ROADMAP §2.

### 01.10.2026 — docs: комплект документации 1.1 + проприетарная лицензия — **DONE**

- **`docs/API.md`**: шапка 165 → **172 роута** (пересчёт декораторов скриптом:
  146 login / 44 admin / 3 открытых), +7 строк 1.1 (`GET /api/dashboard`,
  `GET /capabilities`, `GET /api/capabilities`, `GET /roles`,
  `POST /roles/<rid>/apply`, `GET /api/roles`,
  `POST /api/roles/<rid>/apply`), `/inventory/device/<ip>` → redirect,
  итог таблиц = 172 (заодно поправлен старый хвост «24» в system-секции).
- **`docs/Модули.md`**: поля `module.json` (version/source/permissions/hardware),
  новые разделы «Статусы модулей» (таблица приоритетов compute_status),
  «Роли» (default/media/network, ALWAYS_ON, roles.json, compat-check),
  «Возможности платформы» (reliability, TOOL_PROBES, dashboard); строки
  роутов system/inventory/module_manager обновлены.
- **`README.md`**: h1 «Lan-discovery 1.1», раздел «Что нового в версии 1.1»,
  172 роута, структура репо с core 1.1 (capabilities/roles/dashboard,
  static/style.css, ROADMAP/UI_UX_AUDIT).
- **Счётчики**: `docs/Home.md` (165→172, строка 1.1 в «Что умеет»),
  `docs/Архитектура.md` (172), `docs/Безопасность.md` (172 роута,
  44 admin). Исторические числа в ROADMAP/UI_UX_AUDIT не тронуты.
- **Лицензия**: своя **проприетарная** `LICENSE` (русская; личное
  некоммерческое — свободно; коммерция/распространение — только по
  письменному разрешению; гарантии: «как есть», ответственность за
  бэкапы/системные операции — на обладателя; авто-прекращение при
  нарушении) — установлена **во все 5 репо**: `Lan-discovery-1.1`
  (`34e914d`, CI ✓), `Lan-discovery-ARM` (`cc0abda`), `Lan-discovery-docs`
  (`b0c72fa`), `Lan-discovery-modules` (`5430f68`, + секция в README),
  `Lan-discovery-demo` (`c619885`). `tools/sanitize_docs.py` теперь
  копирует LICENSE в публичную копию docs автоматически; sanitize
  прогнан — leaks 0.
- Коммиты: `776d6cb` (docs) + `34e914d` (license), CI success.

### 01.10.2026 — tools: PHASE 16 №61 дрейф-контроль sync_check — **DONE**

- **`tools/sync_check.py`** в репо: сверка md5 «git ↔ сервер» без
  скачивания файлов — серверный обход (код встроен, выполняется по
  SSH как `python3 - <prefix>`) даёт `md5raw  md5norm  rel`, поэтому
  EOL-only (CRLF↔LF) определяется сразу; сравнение с `git ls-files`.
- Режимы: SSH (Windows — plink, пароль только из env `LAN_SSH_PASS`;
  POSIX — ключ), `--filelist` (готовый файл, без SSH), `--include-docs`,
  `--repo`. Exit: 0 — синхронно, 1 — расхождения, 2 — ошибка запуска.
- **Зона деплоя**: SKIP_LOCAL (не сверяются) — docs/ (кроме
  `--include-docs`), репозиторный инвентарь (AGENTS/ROADMAP/README/
  LICENSE/.github) и deploy-инвентарь (`deploy/`, `deploy.py`,
  `remote_edit.py`, `restore_server.py` и т.п. — запускаются из репо,
  панелью не исполняются).
- **`tests/unit/test_sync_check.py`** (+10): парсинг 3-/2-колоночного
  filelist и ERR-строк, skip-правила, exact/EOL/content/only_* в
  `compare()`, `local_snapshot` на git-фикстуре, CLI `--filelist`
  (exit 0/1/2). На X96: **10 passed**.
- Первый же прогон поймал **реальный дрейф**: `tools/sanitize_docs.py`
  был изменён сегодня в репо без заливки на сервер (закрыт: бэкап
  `*-backup-s61-*` + заливка), плюс `only_local=18` — deploy-инвентарь,
  убран в SKIP.
- Деплой на X96: `tools/sync_check.py`, `tools/sanitize_docs.py`,
  `tests/unit/test_sync_check.py`.

### 01.10.2026 — core: PHASE 16 №59 двойное сканирование (лидер/стоп) — **DONE**

- `core/discovery.py`: `_scan_enabled()` (`network.scan_enabled`, default
  `true`, bool/строки `1/true/yes/on`) + guard в начале итерации
  `scan_loop` (выключено → спит `scan_interval`, сеть не трогает) +
  поле `scan_enabled` в `get_scan_status()`; `modules/system_routes.py`:
  `last_discovery.scan_enabled` в `/api/health`. Ручной `POST /api/scan`
  и статус при выключенном фоне работают.
- **Топология**: лидер — X96 (1.1, default true, settings не менялся);
  на OP (1.0) в `/etc/lan-discovery/settings.json` явно
  `network.scan_enabled: false`.
- Деплой: X96 (бэкапы `core/discovery.py`/`system_routes.py`
  `*-backup-s59-*`, pytest `test_discovery.py` **16 passed**); backport
  того же патча в клон `Lan-discovery-ARM` и на OP (бэкапы тех же файлов
  + `settings.json` `*-backup-s59-*`, `py_compile`, restart).
- Проверка `/api/health`: X96 `scan_enabled=true`, `scan` растёт
  (01.10.2026 17:28:22); OP `scan_enabled=false`, `scan=null` спустя
  40+ сек — фонового опроса нет.
- Тесты (+2): `test_scan_enabled_flag` (bool/строки/default), обновлён
  `test_get_scan_status_shape`. Документация: `docs/Конфигурация.md` —
  ключ `network.scan_enabled` (строка таблицы network).

### 01.10.2026 — core: PHASE 16 №60 локализация CDN + CSP — **DONE**

- **Vendor** в `static/vendor/`: xterm.js 5.5.0 (`xterm.min.css`,
  `xterm.min.js`), addon-fit 0.10.0, socket.io-client 4.7.5
  (`socket.io.min.js`, jsdelivr — cdn.socket.io не отдавал файл целиком);
  `templates/apps/terminal.html` переведён на локальные пути
  (`?v=20261001d`), внешних CDN в шаблоне не осталось — терминал и
  панель работают без интернета.
- **CSP** в `_security_headers` (`app.py`, все ответы): `default-src
  'self'`, `script-src 'self' 'unsafe-inline'` (без внешних скриптов),
  `style-src` inline, `connect-src 'self' ws: wss:
  https://cdn.jsdelivr.net` (socket.io + runtime-курсы валют из
  `currencies.py`), `frame-src 'self' http: https:` (iframe
  Transmission на другом порту = другая origin), `object-src 'none'`,
  `base-uri`/`form-action`/`frame-ancestors 'self'`. Внешние `<a href>`
  (github/netdata/restore) CSP не блокируются.
- Аудит перед CSP: в templates нет WebSocket/fetch наружу, нет
  audio/video/blob, внешних `url()` в css нет; `make_demo.py` режет
  внешние script/link — после локализации этих тегов не остаётся.
- Тесты (+2 unit, live обновлён): `test_csp_header_present`,
  `test_terminal_vendor_local_only` (vendor существует >1KB, CDN нет);
  на X96: unit **138 passed**, live **15 passed** (CSP-заголовок
  подтверждён против живой панели), vendor отдаётся 200.
- Документация: `docs/Безопасность.md` — пункт CSP+vendor в «Что уже
  сделано (hardening)».
- Деплой X96 (бэкапы `app.py`, `terminal.html` `*-backup-s60-*`,
  8 файлов, py_compile, restart).

### 01.10.2026 — tools: PHASE 16 №62 регресс-пентест (оба узла) — **DONE**

- **`docs/Пентест.md` заполнен** по прогону 01.10 на X96 (1.1) и
  Orange Pi (1.0): все разделы 1–8, результаты совпали; SSH-часть
  (периметр/секреты/systemd/таймеры/логи), HTTP-часть (curl-сессии
  c csrf, временные юзеры `pt62guest`/`pt62user` с bcrypt и удалением
  из бэкапа, python-socketio для connect-guard).
- **Находка и фикс: guest `GET /api/settings` → 200** (чек-лист
  требует отказ): `@login_required @admin_required` на
  `api_settings_get` (порядок: аноним → 302, guest → 403); UI этот
  роут не читает (grep по шаблонам пуст); backport в 1.0 и на OP.
  Unit `test_guest_cannot_read_settings` (guest 403 / admin 200),
  live-чек на обоих узлах → 403.
- **Находка и фикс: `restore-server` enabled и слушал 8081 на обоих
  узлах** — `systemctl disable --now` (аварийный `start` работает).
- **Подтверждено live**: rate-limit 5/300с (верная пароля в окне
  блокировки отклоняется, рестарт снимает), enabled=false теряет
  сессию, min-8 пароля (400/200), трейверсал filemanager → 400,
  `_valid_host` отклоняет 6 shell-payload, XSS в имени устройства
  экранируется (`&lt;img`), SocketIO: аноним connect refused / admin OK,
  заголовки+CSP, health-кэш 30с, ноль Traceback/секретов в журнале,
  таймеры weather/backup отработали, юнит hardening как PHASE 8.
- **Residual'ы §8** (п.6–8): legacy SHA-256 у `user`/`guest`, заголовок
  `Server` с версиями Werkzeug/Python, WAN-проброс не проверяется
  (X96 без ufw, слушаем 0.0.0.0), SSRF в IPTV-add (схема URL не
  валидируется). П.1/п.5 §8 закрыты (№59/№60).
- Тесты: unit **139 passed** (X96), live **15 passed** (до правок
  чек-листа; CSP-чек включён). Бэкапы: `core_routes.py`
  `*-backup-s62-*` (оба узла), users.json `*-backup-s62-*` (юзеры
  восстановлены, device name восстановлен).

### 01.10.2026 — PHASE 16 №63: gap'ы §2 (P6/P5/P10) — **DONE**

- **P10 семверы/чейнджлог:** `APP_VERSION "0.9.0" → "1.1.0"`
  (отображается в `/api/health`, `/api/system/health` и на «О
  системе → Программное окружение → Версия панели»), новый
  `CHANGELOG.md` (Keep a Changelog: 1.1.0/1.0.0 из git-истории),
  ссылка в README; `update.sh` пишет `version` (grep APP_VERSION) в
  `meta.json` рядом с `git_rev`.
- **P6 выбор интерфейсов скана через UI:** `GET /api/network/ifaces`
  (admin; `ip -br -4 addr`, без lo), в блоке «Система → Настройки»
  чекбоксы интерфейсов (`network.scan_ifaces`, отмечаются текущие,
  пустой выбор → дефолт движка) + тумблер `network.scan_enabled`
  (№59); **фикс:** `POST /api/settings` переведён на deep-merge —
  раньше частичный POST затирал весь `network` (терялись
  `self_ips`/`wifi_ifaces`/`scan_ifaces`), проверено live: `self_ips`
  уцелели при частичном POST.
- **P5 identity MAC+IP:** в `reconcile` ветка «MAC уже известен под
  другим IP»: новая запись наследует `name`/`device_type`/`first_seen`,
  `is_new=0`, `appearances+1`; событие **IP_CHANGED** (info,
  `metadata.old_ip`) вместо NEW; прежний IP уходит в OFFLINE штатным
  механизмом misses; `EVENT_SEVERITY["IP_CHANGED"]="info"`; история
  показывает бейдж через else-ветку. Тесты: `test_reconcile_mac_move_ip_changed`,
  `test_reconcile_unknown_mac_still_new` (+обновлённый stats-контракт).
- Документация: `CHANGELOG.md`, `docs/API.md` (173 роута: +ifaces),
  `docs/Архитектура.md` (identity в строке discovery), README (ссылка).
- Деплой X96 (бэкапы `*-backup-s63-*`, `*-backup-s63b-*`): unit
  **149 passed** (+10), live **15 passed**; ручной `POST /api/scan`
  → `stats {new:0, online:1, offline:1, ip_changed:0}`; version
  `1.1.0`; ifaces `[eth0 UP, wlan0 UP]`. OP (1.0) — не бэкпортится
  (новая функциональность, не горячий фикс).

### 01.10.2026 — PHASE 16 №64: харддинг-резидуалы — решение — **DONE**

- **«root by design» подтверждён на очередной квартал (до
  01.01.2027)** — в `docs/Безопасность.md` → «Модель угроз»
  зафиксировано окно действия и **триггеры немедленного пересмотра:**
  (1) любой удалённый доступ к панели вне домашнего LAN, (2)
  подтверждённый WAN-проброс 8080, (3) появление второго
  не-админ-пользователя в users.json.
- **reverse-proxy/TLS отложен окончательно** на это окно: цена
  (переработка SocketIO, внутренний CA, двойной слой мониторинга)
  не оправдана закрытым LAN-периметром; компенсации в окне —
  bcrypt+TTL+rate-limit 5/300с, CSP без CDN, CSRF, systemd
  hardening, регресс-пентест 01.10.
- **non-root отложен окончательно** (вместе с TLS/прокси): sudo-мост
  для systemd/дисков/сети/bluetooth дороже, чем root-модель, при
  которой admin-доступ и так равен root (терминал/filemanager).
- Попутные правки доков: периметр (ufw только на OP, WAN-проброс не
  подтверждался пентестом, restore-server выключен №62); в
  «Известных ограничениях» убран устаревший «нет rate-limit»
  (есть с P0-5).

### 01.10.2026 — PHASE 16 №65: residual UI (eMMC/clone/HDD на X96) — **DONE**

- **Обход по панели X96 (boot с SD `/dev/mmcblk1p2`, без HDD):**
  `/system`, status-bar шапки, `/capabilities`, `/about`, `/help`,
  monitoring — найдены и исправлены три места, где мёртвые блоки
  были видимы/активны:
  1. **кнопки eMMC-бэкапа** (`sys-emmc/block.html`) были активны
     при статусе «НЕДОСТУПЕН» — теперь `disabled` + `title=reason`
     (как у clone); серверный `POST /system/backup` уже отдавал 409;
  2. **`POST /system/backup-test` был без guard'а** — добавлен
     `emmc_backup_guard` → 409 с причиной (не запускает zstd);
  3. **строка HDD в блоке «Плата»** рендерилась всегда («HDD: --») —
     добавлен `hdd_present` в рендер `/system`, строка показывается
     только при `hdd_device()`; в **status-bar шапки** eMMC/HDD/SD
     элементы теперь собираются в массив условно: HDD-объём и
     HDD-load не выводятся при `srv_*/hdd_io_ticks == null`
     (раньше показывался мусор «0.0/0.0 ГБ»), eMMC-load — при
     отсутствии `emmc_io_ticks`.
- **Проверено «ок»:** `sys-clone` (clone-btn disabled + title),
  `/capabilities` (absent → «— не обнаружено»), `/about` (типы из
  lsblk), `/help` (динамические `hf.*`-условия), monitoring (нет
  hdd/eMMC строк), `POST /api/clone/start` → error на сервере.
- Тесты: `tests/unit/test_ui_residual.py` (+5: disabled-кнопки при
  guard False/True, скрытие/показ строки HDD, guard backup-test);
  на X96: unit **154 passed**, live **15 passed**; live-грепы:
  `startBackup()" disabled`=1, `id="pi-hdd"`=0, backup-test=409.
- Бэкапы: `*-backup-s65-*` (system_routes.py, sys-board/sys-emmc
  block.html, base.html).

### 01.10.2026 — PHASE 16 №66: residual-фиксы (SSRF IPTV + Server) — **DONE**

- **SSRF IPTV (§8.8, находка №62):** `POST /system/iptv/add` теперь
  принимает только `http`/`https` с непустым netloc (`urlparse`);
  `file://`, `gopher://`, `ftp://`, `javascript:`, `//host`,
  `http без netloc` → тихий редирект **без сохранения**. Внутренние
  адреса и localhost по http допустимы — LAN-модель (зафиксировано
  в §8.8 и чек-листе). `modules/media_routes.py`.
- **Server-заголовок (§8.3, находка №62):** Werkzeug шлёт свой
  `Server` в `send_response()` — **после** заголовков приложения,
  поэтому правка в `after_request` давала два заголовка. Фикс:
  подмена `WSGIRequestHandler.version_string` → `"lan-discovery"`
  в `app.py`; наружу уходит ровно один заголовок без версий
  Werkzeug/Python. На OP (версия 1.0) остаётся до апдейта.
- Тесты: `tests/unit/test_iptv_ssrf.py` (+11: 7 отказов/4 приёма,
  параметризованные), `test_security.py` (+1 `version_string`) →
  на X96 unit **166 passed**, live **15 passed**.
- Live на X96: `curl -sI` → 1× `Server: lan-discovery`; IPTV-греп
  с CSRF: file:// и gopher:// → 302, плейлисты 14→14 (не сохранены),
  http → 302, 14→15 (сохранён), удаление тест-плейлиста → 14.
- Чек-лист `docs/Пентест.md`: пункты SSRF и Server-версии → `[x]`
  с описанием фикса. Бэкапы X96: `*-backup-s66-*` (app.py,
  media_routes.py).

### 01.10.2026 — PHASE 16 №67: автозапуск дрейф-контроля — **DONE**

- **§8.2 закрыт полностью:** `tools/sync_check_daily.cmd` (обёртка:
  cwd=репо, `chcp 65001`, пароль из локального файла, лог с
  таймстампом и exit-кодом) + `tools/setup_sync_task.ps1`
  (регистрация Windows-задачи `LanDiscovery-SyncCheck`: ежедневно
  09:30, `StartWhenAvailable`, лимит 5 мин, `-SshPass` сохраняет
  пароль в `%LOCALAPPDATA%\lan-discovery\ssh_pass.txt` — **вне
  репозитория**). Файлы `.ps1` — UTF-8 **с BOM** (PS 5.1 без BOM
  читает кириллицу как ANSI и падает парсером).
- Задача зарегистрирована и проверена запуском вручную:
  `LastTaskResult=0`, в логе `exact=179` (exit=1 из-за ещё
  незакоммиченного `test_iptv_ssrf.py` — после коммита станет 0).
- `AGENTS.md` §7 дополнен: автозапуск, путь лога, где хранится пароль.

### 01.10.2026 — PHASE 16 №68: демо-слепок под STEP 12 + №59–67 — **DONE**

- На X96: `LAN_PANEL_PASS=... tools/make_demo.py /tmp/demo` →
  156 файлов (4.8M), склейка `01.10.2026 20:19`, name-fixes 4
  (персональные имена → «Телефон 1-4»), `login: ok`, pages 63,
  api snapshots 83, settings scrubbed, json sanitized 84.
- Локально: `demo_lint.py` → **LINT OK: 156 files** (net/secret
  leaks 0); пуш в `Lan-discovery-demo` (`dded0f1`), Pages build
  **success**, https://kotmartovskiy.github.io/Lan-discovery-demo/
  отдаёт слепок 01.10.2026 (HTTP 200, marker в index).
- **Инцидент:** зеркальное копирование слепка в клон затёрло
  не-слепочный `LICENSE` (в коммите `dded0f1`) — восстановлен
  отдельным коммитом `e36c3a0` из `c619885`. Урок: при обновлении
  демо-слона сохранять служебные файлы клона (LICENSE и др.).
- **PHASE 16 закрыта полностью (59–68)**; релиз отмечен тегом
  `v1.1.0` (запушен).

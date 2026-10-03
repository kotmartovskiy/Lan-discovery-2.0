# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/). Версия панели —
`APP_VERSION` в `core/version.py` (§34; показывается в `/api/health`,
`/api/system/health` и на странице «О системе»). Git-тег `vX.Y.Z`
ставится на релиз.

## [Unreleased]

### Исправлено
- **FINAL BETA AUDIT (pre-release):** блокеры аудита закрыты, unit 413
  passed / 5 skipped (+16 тестов к базовым 402; CI ожидает 418
  passed); live-прогон на стенде N6 — после деплоя.
- **A-01 установка:** `install.sh step_config` сидит `users.json`
  (admin/1234, bcrypt с fallback legacy sha256 и ленивым re-hash при
  входе; идемпотентно — существующий файл не трогается, запись
  атомарная) — чистая Debian получает вход, как обещают install.sh и
  `docs/2.0/INSTALL.md`; `step_code` кладёт `network_check.py`.
- **C-02 сеть:** `network_check.py` возвращён в репо (со стенда;
  раньше существовал только в `/opt/...` и не входил в git),
  `NETWORK_CHECK_SCRIPT` вычисляется от `__file__` (живёт при
  `--prefix`/BindPaths), запуск через `sys.executable`.
- **B-03 события→automation:** `add_event(..., out=)` +
  `events.notify_all()` — discovery (фоновый `scan_loop` и ручной
  скан-job) коммитит транзакцию и только потом делает fan-out
  подписчикам, без записи в чужую открытую транзакцию (busy/deadlock);
  payload несёт каноническое namespace-имя (`device.offline`), в БД
  остаётся legacy-имя — dual-read сводит обе стороны. Правила §17 на
  `device.*` теперь реально срабатывают на сканы.
- **B-04 RBAC:** `@can_edit` на 45 mutation-маршрутах (notes ×3,
  inventory, media ×29, network ×12) — роль guest (просмотр) получает
  403 на запись во всех модулях; инвариант-тест сканирует
  `modules/*.py` + `app.py`, параметризованный guest-403 — на
  репрезентативных эндпоинтах.
- **B-05 portability:** `sys.path` в `app.py` — каталог самого `app.py`
  (с dedup) вместо хардкода `/opt/lan-discovery` — запуск из другого
  префикса не подхватывает чужое ядро.
- **D-07** `auth.save_users` пишет атомарно (tmp + `os.replace`) —
  обрыв не оставляет обрезанный `users.json`.
- **D-08** `login_required`: `/api/*` без сессии → JSON **401** (раньше
  302 на HTML-форму ломал fetch-клиенты), страницы — 302 как раньше.
- **D-09** demo-барьер (§28): изменяющие методы на HTML-путях тоже 403
  (раньше проверялись только `/api/*`); `/login` — исключение, вход в
  демо жив.
- **D-10** два windows-only теста получили `skipif` по `/proc`
  (диски/lsblk), **D-11** ссылка на `APP_VERSION` в CHANGELOG
  указывает `core/version.py` (§34).

## [2.0.0] — 03.10.2026

### Добавлено
- **Release preparation (PHASE 2.0-21, §33 Ph.10/§29/§28):** документация §29 — 11 guides в ``docs/2.0/`` (ARCHITECTURE: целевая модель, границы Core/Module/Role/UI, инварианты слоёв; CORE_API: контракты всех subsystems; MODULES: манифест v2 + uniform ``register_routes(app, ctx)``; CAPABILITIES: 11 групп/reliability; ROLES: ``roles/*.json`` + compat-check; NETWORK: ``net.transaction``/discovery; STORAGE: ``ROOTS/path``; JOBS; SECURITY: secrets/trust/syschange; DEVELOPMENT: тесты/CI/инварианты; PORTING: чек-лист устройства и запрет ``if x96max``) + guides Ph.10 (INSTALL/UPGRADE/MIGRATION) + ``RELEASE_NOTES.md`` (draft v2.0.0; тег — по указанию, ``APP_VERSION`` — ``core/version.py``, §34). **Demo mode (§28):** ``core/demo.py`` — API adapter (``before_request`` первым): GET ``/api/*`` → фикстуры ``<dir>/api/*.json`` (кэш 5 с), без фикстуры → безопасный ``{ok:false, demo:true}`` 404, изменяющие методы → 403, без сессии → 401; включение ``settings.web.demo``/``LAN_DEMO=1``, плашка ``.demo-banner`` в ``base.html`` (production HTML не копируется); ``tools/make_demo.py --fixtures`` — снимок только GET-API с санитизацией (секреты/MAC/имена/подсеть), статичное демо-сайтное генерирование сохранено. Тесты: +7 (test_demo_adapter).
- **Portability (PHASE 2.0-20, §30):** плато-специфика убрана из кода панели — только capabilities: манифест ``sys-board`` (``name``/``block.title`` «X96 Max» → «Плата»; реальное имя платы рендерится из device-tree через ``board_title()``); справка ``hf.lan`` — generic-подписи хостов («Основная панель», «Удалённый сервер», «Рабочая станция», …) + гейт по ``network.subnet`` (список виден только если primary в настроенной подсети, иначе таблица «Сетевые устройства» скрыта); дефолт ``monitoring.hosts`` → пустой список + empty state в ``monitoring.html`` (хосты мониторинга — только из настроек); help.md модулей (monitoring, wifianalyzer); докстринги (``panel_name``, ``discovery._scan_enabled``, ``board_title``, ``_help_facts``). Инвариант закреплён тестом: grep-запрет 16 имён плат/вендоров в коде панели (``core``/``modules``/``templates``/``static``/``app.py``/``install.sh``; ``tests``/``tools`` — исключение), generic-манифест sys-board, гейт ``hf.lan`` по subnet. Тесты: +3 (test_portability).
- **UI 2.0 shell (PHASE 2.0-19, §22–23):** Application Shell → Navigation → Module Page: модель разделов §22 (``SECTIONS`` HOME/NETWORK/MONITORING/STORAGE/APPLICATIONS/HARDWARE/SYSTEM/ADMIN в ``core.module_loader``), section-поля у всех пунктов навигации (``nav_sections/active_section``, ``nav_groups(admin, section)`` — фильтр активного раздела), строка разделов в ``base.html`` (иконки, ``aria-current``, responsive) над доменными вкладками. HOME = dashboard (``/``, ``templates/dashboard.html``): 6 ответов §22 (нормально?/происходит/устройства/jobs/проблемы/предупреждения) с loading/error states; devices переехал на ``/devices``, после входа — на HOME. Новый раздел STORAGE (``/storage`` на read-only контрактах ``core.storage``, пустые состояния). Единые компоненты §23: ``window.toast()`` + ``window.confirmDialog()`` (``<dialog>``), 3 legacy ``alert()`` заменены на toast. Тесты: +12 (test_shell_20), правки responsive/a11y под ``/devices``.
- **Installer 2.0 (PHASE 2.0-18, §25):** ``install.sh`` по цепочке §25 — preflight (python3/python ≥3.9, arch/kernel, дистрибутив из ``/etc/os-release`` с WARN для не-Debian/Ubuntu, apt/dpkg, место на диске) → hardware detection (``tools/hw_detect.py`` — переиспользует ``core/hardware.detect_platform()``, тот же источник что ``/api/health``; stdlib-only, работает до venv; отчёт ``hw-detect.json``) → dependencies (критичные обязательны, опциональные best-effort по одному — minimal-система устанавливается, неудачи пишутся в отчёт) → core (code/venv) → modules (проверка ``discover_modules()``) → configuration → systemd → health check (python3 urllib вместо curl, порт из settings, валидация JSON/version/user_version). Тесты: +3 (chain-порядок ``main()``, ``bash -n`` + dry-run, hw-detect report); CI — шаг dry-run Installer.
- **Appliance smoke test (PHASE 2.0-17, §27):** единая интеграционная цепочка §27 (``tests/unit/test_appliance_smoke.py``, 12 шагов): чистая установка → login → hardware detection (``/api/health``, ``db.user_version=3``) → capabilities → discovery (fake-скан → job) → module installation (notes, state в tmp) → role application (default) → jobs → configuration change (settings с откатом) → версия из ``core.version`` → failure simulation (``syschange.BACKUP_ROOT`` в tmp, FakeApt-сбой) → rollback (пакеты сняты, модуль не помечен установленным, настройки прежние); live-версия цепочки против живого стенда (``tests/live/test_live_appliance_smoke.py``, ``LAN_PANEL_URL``, skip при ``user_version<3``). Фикс: ``module_manager._module_install_job`` — ``set_module_status`` только после ``result.ok`` (раньше сбой apt+rollback всё равно помечал модуль установленным). Тесты: +1 unit (локально 370 passed).
- **System rollback (PHASE 2.0-16, §26):** ``core/syschange.py`` — транзакции системных изменений ``preflight → backup → apply → verify → rollback`` (``run()``): preflight — доступность apt + симуляция ``apt-get -s`` и валидация файловых операций; backup — снимок в ``/var/lib/lan-discovery/rollback/<id>/`` (``manifest.json``, состояния dpkg до, ``selections.txt``, копии файлов); verify — установка пакетов/содержимое файлов (+ кастомная проверка); rollback (в т.ч. публичный ``rollback(txn_dir)`` для ручного/смоук-каталога §27) — снятие новых пакетов, восстановление файлов и dpkg-selections. Потребитель: apt-шаг ``module_manager._install_manifest`` (логи шагов в job, ``SystemChangeError`` → ``ok: false``). Разделение сохранено: application rollback — в ``update.sh``. Тесты: +9 (FakeApt-раннер: порядок шагов, preflight/apply/verify-откаты, file_write, потребитель).
- **Module contract v2 (PHASE 2.0-15, §19/§24):** uniform module context — единственная сигнатура ``register_routes(app, ctx)`` во всех 12 роут-модулях; ``app.py`` (assembly root) собирает ``ctx`` (auth-декораторы, ``page_data``, ``_cmd``/``_cfg``, ``DB``, ``service_state``, ``GAMES_DIR``, ``check_internet_cached``/``weather_current`` и др.) и единообразно вызывает ``register_*(app, ctx)``; модули больше не импортируют ``app`` — инверсии **modules→app = 0** (было ×9+: ленивые ``from app import`` в обработчках заменены на ctx-замыкания; ``_cfg`` в бизнес-модулях → ``core.config.get``; ``APP_VERSION`` → ``core.version``); builtin-манифесты (33 шт.) получили v2-поля ``version: 2.0.0``, ``capabilities`` (6 hardware-зависимых), ``/modules`` показывает версию вместо «Unknown». Тесты: +3 (uniform-сигнатура, version/validate, capabilities).
- **Config & Secrets (PHASE 2.0-14, §20–21):** ``core/secrets.py`` — программный API ``get/set/delete/keys`` (kv.json отдельно от settings, значения Fernet at rest, файл 0600, атомарная запись) + криптография UI-менеджера (Fernet/legacy-HMAC, ключ ``secret.key``) перенесена из ``modules/core_routes`` (compat-алиасы ``_secrets_encrypt/_secrets_decrypt`` сохранены); токен каталога модулей читается из secrets (``modules_catalog_token``) с fallback на legacy-место в settings (§32); классы конфигов §20 задокументированы в ``core/config``, единый справочник путей (``SETTINGS_PATH``/``MODULES_STATE_PATH``/``ROLES_STATE_PATH`` — потребители module_loader/roles/module_catalog). Тесты: +10.
- **Automation (PHASE 2.0-13, §17):** ``core/automation.py`` — компактный appliance engine Event→Rule→Action (не HA-клон): правило ``{name, enabled, event, actions[], cooldown_sec}`` в таблице ``automation_rules`` (ensure-схема), матчинг событий dual-read (правило ``OFFLINE`` ловит ``device.offline``), cooldown/fired_count, защита от петель (события от automation не триггерят правила), реестр действий ``register_action`` (встроены ``log``/``event``; модули добавляют свои), engine подключён через ``events.subscribe`` (``automation.start()`` в app); CRUD API ``/api/automation/rules`` (+toggle/DELETE, can_edit), страница ``/automation`` (nav «Система», admin). Тесты: +11.
- **Events 2.0 (PHASE 2.0-12, §16):** канонические namespace-имена ``device./network./storage./camera./job./module./system.`` (``NAMESPACE_EVENTS``, строгие для ``emit``, severity в той же карте); фасад ``events.emit()`` (INSERT + fan-out) и ``events.subscribe()`` (in-process подписка, ошибки подписчиков не роняют писателя — фундамент Automation §17); dual-read в ``list_events``: фильтр ``event=ONLINE`` видит и ``device.online`` (лента/UI не ломаются); первый потребитель — core/jobs (``job.started/completed/failed/cancelled`` с metadata job_id/job_type); ``wait()`` возвращает терминальный статус только после финализации persist+событие (read-after-wait). Тесты: +11.
- **Device identity (PHASE 2.0-11):** ``core/identity.py`` — стабильный ``device_id`` (``mac:<mac>``, fallback ``ip:<ip>``; присваивается один раз, не переписывается при смене MAC) и ``ip_history`` (цепочка IP устройства); миграция БД v3 (аддитивно к ``devices.ip`` PK, backfill существующих строк); ``reconcile`` присваивает/наследует device_id при переезде (identity P5 сохранён); аддитивный ``GET /api/device/<ip>/identity`` → ``{device_id, ip, mac, hostname, ip_history}``; события и UI-URL не тронуты (namespace событий — фаза 2.0-12). Тесты: +6.
- **Core independence (PHASE 2.0-10):** ``core/version.py`` — единственный источник ``APP_VERSION`` (§34); ``core/db.py`` — владение схемой sqlite (миграции/ensure/init/get_db перенесены из devices_routes); инверсии ``core→app/modules`` устранены (0 из 8: get_db/DB/_cfg/APP_VERSION → core.db/core.config/core.version), старые импорты работают через compat-реэкспорт (§32); retention/настройки core — через ``core.config``.
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

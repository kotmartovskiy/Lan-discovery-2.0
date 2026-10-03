# CORE_API — контракты ядра (спека §29)

Сигнатуры зафиксированы в [`Архитектура-2.0.md` §6](../Архитектура-2.0.md)
и уточнялись по ходу фаз. Правило: контракт — только с потребителем
(модуль/страница) и тестом (§36). Потребитель видит ядро через `ctx`
(см. [MODULES.md](MODULES.md)) — модули не импортируют `app`.

## core/services.py (фаза 2.0-2)

```python
services.status(name) -> {"state": ..., "active": bool, ...}
services.start/stop/restart(name) -> ok|error
services.enable/disable(name) -> ok|error
services.logs(name, lines=100) -> str
services.health(name) -> ...
```

## core/jobs.py (фаза 2.0-3)

```python
jobs.submit(type, fn, *, cancelable=False, meta=None) -> job_id
jobs.get(job_id) -> {id,type,status,progress,started_at,finished_at,logs,result,error,cancelable}
jobs.list(status=None) -> [job]        # новые сверху
jobs.cancel(job_id) -> ok|error
```

Долгие операции (установка модуля, бэкапы, скан, системные операции)
идут через jobs. События `job.started/completed/failed/cancelled`
(`core/events`), `wait()` читает терминальный статус после
финализации (read-after-wait). HTTP: `GET /api/jobs`, `GET /api/jobs/<id>`.

## core/network.py (фаза 2.0-6)

```python
net.inspect() -> {...}                 # интерфейсы/адреса/маршруты
net.transaction(desc) -> Tx            # tx.prepare() → tx.apply() → tx.verify()
                                       #           → tx.commit() / tx.rollback()
```

## core/storage.py (фаза 2.0-7)

```python
storage.roots() -> {"media": "/srv/media", "data": "/srv/data", "backup": "/srv/backup"}
storage.disks()/mounts()/smart(dev) -> ...
storage.path("media", *parts) -> str   # единая точка для модулей
```

Потребители: страница `/storage`, IPTV-каталоги, бэкапы. Подробнее —
[STORAGE.md](STORAGE.md).

## core/events.py (фаза 2.0-12)

```python
events.emit(name, *, con=None, ip=None, hostname=None, mac=None, ...)  # namespace-имена
events.subscribe(handler) -> ...        # in-process fan-out
events.list_events(con, limit=500, event=None, severity=None, ip=None, ...) -> [...]
events.cleanup_old_events(con, days) / retention_loop(interval=86400)
```

Канонические namespace: `device./network./storage./camera./job./module./system.`
(`NAMESPACE_EVENTS` — severity и допустимые имена).

## core/automation.py (фаза 2.0-13)

```python
automation.register_action(action_type, fn)   # встроены: log, event
automation.action_types() -> [str]
automation.start() / automation.stop()        # подписка на events
```

Правила (`automation_rules`): `{name, enabled, event, actions[], cooldown_sec}`,
защита от петель, cooldown/fired_count.

## core/roles.py (фаза 2.0-5)

```python
roles.load_roles(force=False) -> {id: манифест}   # roles/*.json
roles.get_role(rid) / active_role() / set_active(rid)
roles.role_module_ids(rid) -> [module_id]
roles.apply_role(rid) -> ...                      # compat-check: role_blockers(rid)
roles.validate_role(m, stem=None) -> [ошибки]
roles.roles_overview() -> ...
```

Подробнее — [ROLES.md](ROLES.md).

## core/capabilities.py (фаза 2.0-1)

```python
capabilities.collect() -> {group: {...}, checked_at: ts}   # 11 групп, TTL-кэши
```

Подробнее — [CAPABILITIES.md](CAPABILITIES.md).

## core/secrets.py (фаза 2.0-14, §21)

```python
secrets.get(key, default=None) / set(key, value) / delete(key) / keys()
secrets.encrypt(text) / decrypt(data) / load_key() / _fernet()
```

`kv.json` отдельно от settings, значения Fernet at rest, файл `0600`,
атомарная запись.

## core/config.py (§20)

```python
config.load(path=None) -> dict
config.get(section, key, default=None, path=None)
config.save(data, path=None) / clear_cache(path=None)
```

Единый источник путей конфигов: `SETTINGS_PATH`,
`MODULES_STATE_PATH`, `ROLES_STATE_PATH`.

## core/db.py (фаза 2.0-10)

```python
db.get_db()                       # sqlite connection (+PRAGMA)
db.init_db_schema(force=False)    # миграции v1/v2/v3, ensure-таблицы
```

Владение схемой (device identity v3, automation, jobs, events).
Retention — через `core.config`.

## core/version.py (фаза 2.0-10, §34)

```python
version.APP_VERSION -> "2.0.0"    # ЕДИНСТВЕННЫЙ источник версии ядра
```

Модули имеют свои версии (`module.json`/`index.json`) — не здесь.

## core/module_loader.py / module_catalog.py (§19/§24)

```python
loader.discover_modules(force=False) -> [manifest]
loader.module_status(mid) / set_module_status(mid, installed, enabled)
loader.nav_items()/nav_groups(admin, section)/nav_sections(admin, path)
loader.section_for(item) / active_section(path) / active_page(path)
loader.modules_with_status() / app_items() / block_items(slot) / help_sections()
```

Подробнее — [MODULES.md](MODULES.md).

## core/hardware.py / core/process.py / core/identity.py / core/syschange.py

```python
hardware.detect_platform() -> {platform, board, arch, ...}   # device-tree/sysfs
process.run(...)                                              # единый запуск внешних тулов
identity: присваивание device_id (mac:<mac> / ip:<ip>), ip_history
syschange.run(...)  # preflight → backup → apply → verify → rollback (§26)
```

## core/demo.py (фаза 2.0-21, §28)

```python
demo.demo_enabled() -> bool        # env LAN_DEMO=1 | settings.web.demo
demo.fixtures_dir() -> str         # env LAN_DEMO_DIR | settings.web.demo_dir
demo.fixture(path) -> данные | _NO_FIXTURE   # <dir>/api/<path>.json, кэш 5 с
demo.fixtures(force=False) / clear_cache()
```

API-адаптер §28: `before_request` в `app.py` (первый в очереди) —
в demo-режиме GET `/api/*` → фикстура, без фикстуры → безопасный
`{ok:false, demo:true}` 404, изменяющие методы → 403, без сессии → 401;
HTML — production-шаблоны с плашкой (`demo_mode` в контексте).
Фикстуры: `tools/make_demo.py --fixtures <dir>` (санитизация API-снимка).
Схема режима — ARCHITECTURE.md, «Основные потоки».

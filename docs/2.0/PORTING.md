# PORTING — перенос на новое железо (спека §29, §30)

Главная идея: **портинг не должен требовать правок кода**. Плата/вендор
нигде не зашиты (§30) — система определяет, что у неё есть
(capabilities), и ведёт себя по этому.

## Классы hardware (подтверждено аудитом)

| Класс | Примеры | Статус |
|---|---|---|
| ARM (Allwinner) | Orange Pi | 1.0 (боевой), 2.0 — при стенде |
| ARM (Amlogic) | X96 Max | 1.1 (боевой), 2.0 — при стенде |
| x86_64 | PC/server | dev + CI |

См. [`PORTABILITY_AUDIT.md`](PORTABILITY_AUDIT.md) — что проверено,
инвариант в `tests/unit/test_portability.py`.

## Что делает система сама

1. **Hardware Detection** — `core/hardware.detect_platform()`:
   `/proc/device-tree/model`, `/sys`, `platform.machine()`; имя платы
   в заголовках — `board_title()` (device-tree → hostname);
2. **Capabilities** — `core/capabilities.collect()`: 11 групп
   (интерфейсы, диски/SMART, radio/SDR, камеры, media, systemd,
   thermal, tools…), состояния `present/absent/unknown` × reliability;
3. **Installer §25** — `install.sh` арх-агностичен (`uname -m` в
   preflight), дистрибутив — WARN для не-Debian, критичные пакеты
   обязательны, опциональные best-effort; `tools/hw_detect.py`
   пишет отчёт `hw-detect.json`;
4. **модули** декларируют `capabilities` в манифесте; отсутствие
   capability → модуль помечен несовместимым (не падает).

## Чек-лист нового устройства

```text
1. Залить репозиторий, ./install.sh (dry-run: ./install.sh --dry-run)
2. GET /api/health            → platform/board/arch из detect_platform()
3. GET /api/capabilities      → нужные группы present, checked_at свежий
4. GET /api/capabilities      → модули с несовместимыми capabilities — badge
5. pytest tests/unit -m "not live"   → зелёные (без железа-специфики)
6. POST /api/scan             → скан/идентичность устройств работают
7. Системные сервисы (systemctl status lan-discovery) + страница /system
```

## Если что-то действительно нужно под плату

Место **только** одно — Hardware Detection / Driver Adapter:

```text
core/hardware.py    — новый probe (device-tree/sysfs/…)
core/capabilities.py — новая группа/probe + reliability
```

Запрещено (падает CI-тест §30):

```text
if x96max: ...        if orangepi: ...      «X96 Max»/«Orange Pi» в коде
```

В модулях, шаблонах, ядре — только capabilities. Тест
`tests/unit/test_portability.py` гоняет grep-запрет 16 имён
плат/вендоров по `core/`, `modules/`, `templates/`, `static/`,
`app.py`, `install.sh`.

## Отличия архитектур, которые учитывает ядро

* **eMMC vs SD vs HDD** — роли дисков через `find_typed_block`
  (паттерны `mmcblk*`/`sd*`), не по имени платы;
* **нет netdata/nmap** — capabilities `tools`/`service` absent →
  UI показывает причину, модули — empty state (§23), не 500;
* **armhf без wheel** — installer ставит критичные пакеты через
  apt (cffi/cryptography/bcrypt), venv создаётся с `--system-site-packages`
  fallback;
* **только WiFi (нет ethernet)** — `network.self_ips`/`scan_ifaces`
  из настроек, скан идёт по доступным интерфейсам.

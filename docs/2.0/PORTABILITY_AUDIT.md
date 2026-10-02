# PORTABILITY_AUDIT — LAN Discovery 2.0 (спека §30, PHASE 2.0-20)

**Дата:** 03.10.2026 · **Статус:** DONE (инвариант закрыт кодом + тестами)

Аудит §30: «нельзя `if x96max:` / `if orangepi:`, если вместо этого
можно определить capability; hardware-specific код — в Hardware
Detection / Driver Adapter». Проверяемые классы hardware:

* Orange Pi / ARM (Allwinner) — рабочий стенд версии 1.0
* X96 Max / Amlogic ARM — тестовый стенд версии 1.1
* x86_64 PC/server — dev-окружение и CI (ubuntu-latest)

Физические прогоны 2.0 на этих платах **не выполнялись**: 2.0 не
деплоится на X96/Orange Pi до отдельного указания (стенд — N6,
решение пользователя). Инвариант §30 доказан отсутствием плато-ветвлений
в коде (§36), а не живым прогоном.

---

## 1. Что уже было generic (проверено, правок не требует)

| Область | Источник | Почему generic |
|---|---|---|
| Model/SoC/уникальные id | `core/hardware.detect_platform()` | `/proc/device-tree/model`, `/sys`, `platform.machine()` — данные, не условия |
| Имя платы в заголовках | `modules/system_routes.board_title()` | device-tree → hostname, вычистка юр.суффиксов |
| Capabilities (11 групп) | `core/capabilities.py` | present/absent/unknown по факту (тулы, интерфейсы, wiphy) |
| Диски/монтирования | `about_data()`, `find_typed_block()` | sysfs/lsblk-паттерны (`mmcblk*`, `sd*`, `nvme*`) — классы устройств, не модели |
| Installer | `install.sh` (2.0-18) | арх-агностичен (`uname -m` в preflight), distro — WARN, не abort; `tools/hw_detect.py` переиспользует `core.hardware` |
| Тесты | `test_installer.py` | уже запрещал «odroid»/«raspberry» в `install.sh` |
| Health/capabilities API | `/api/health`, `/api/capabilities` | `arch`/`machine` — из runtime-детекта |

## 2. Найдено и исправлено в фазе

| Где | Было | Стало |
|---|---|---|
| `modules/sys-board/module.json` | `name`/`block.title` = «X96 Max» | «Плата»; реальное имя — `board_title()` в `block.html` |
| `modules/core_routes.py` → `hf.lan` | подписи «X96 Max (панель)», «Orange Pi (LAN)», «ThinkPad T480» | generic («Основная панель», «Удалённый сервер», «Рабочая станция», …) + гейт: список только если `primary ∈ network.subnet`, иначе `[]` и таблица справки скрыта |
| `modules/monitoring_routes.py` | default `hosts` — 4 хоста конкретной сети («Orange Pi», «Thinkpad T480 …») | default `[]`; empty state в `monitoring.html` («хосты — из `settings.json → monitoring.hosts`») |
| `modules/monitoring/help.md` | «X96 Max (192.168.3.243)…», «На X96 Max: …» | generic-текст (только `monitoring.hosts`, netdata-инструкция без платы) |
| `modules/wifianalyzer/help.md` | «(на X96 Max — `wlan0`)» | «(имя интерфейса, например `wlan0`)» |
| `templates/help.html` | устаревшие пути (`Устройства (/)`), без Хранилища | `Главная (/)`, `Устройства (/devices)`, `Хранилище (/storage)`; таблица сетевых устройств — `{% if hf.lan %}` |
| докстринги | `app.panel_name`, `discovery._scan_enabled`, `system_routes.board_title`, `_help_facts` — упоминания плат | переписаны без имён плат |

Скан: `core/`, `modules/`, `templates/`, `static/`, `app.py`,
`install.sh` — ни одного упоминания плат/вендоров. `tools/sync_check*`
и `tests/` — исключение (инвентарь репозитория 1.1, легально).

## 3. Инвариант (закреплён в `tests/unit/test_portability.py`)

1. **grep-запрет 16 имён** плат/вендоров (`x96`, `orangepi`, `orange pi`,
   `amlogic`, `odroid`, `rockchip`, `rock pi`, `raspberry`, `raspberrypi`,
   `bananapi`, `nanopi`, `pine64`, `librecomput`, `allwinner`, `thinkpad`,
   `tinker`) по всем файлам `.py/.html/.js/.css/.json/.sh/.md` в коде
   панели — новый хардкод упадёт в CI.
2. **sys-board-манифест generic** — без имён плат в `name`/`title`.
3. **Гейт `hf.lan` по subnet** — известные хосты показываются только
   внутри настроенной подсети (на чужой сети справка честно пуста).

## 4. Остаток

* Живой прогон 2.0 на Orange Pi / X96 Max / x86_64 — при стенде
  (N6): проверка device-tree-детекта, capabilities-групп radio/storage,
  установки по `install.sh` на ARM-чипе (см. 2.0-21 Release, §29/§33).
* `modules/recycling.py` использует фиксированный User-Agent
  `Linux armv7l` — не ветвление, а маскировка запроса; при желании
  привести к `platform.machine()` — вне §30 (не влияет на ветвления).

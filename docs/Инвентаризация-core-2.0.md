# Инвентаризация вызовов subprocess/systemctl (PHASE 2.0-2)

**Цель (спека §4):** «Module → Core API → subsystem → Linux» — сначала
зафиксировать все прямые вызовы ОС из кода, потом переносить по одной
функции за фазу (Архитектура §3.2, «не переписывать все разом»).
Пересчёт при каждой фазе, статус — в столбце.

**Как считано:** `rg` по `subprocess.(run|Popen|check_output|check_call|
call)|os.system|os.popen` в `*.py` без `venv/`.

## 1. Итоги

- **71 прямой вызов** в **13 файлах** (на момент инвентаризации);
  после переносов PHASE 2.0-2 — **67**;
- `systemctl` — в 9 py-файлах (включая вызовы через хелперы
  `_cmd`/`_run`: их строки в прямых вызовах не видны);
- аудит 2.0-0 говорил «18/13» — это считало все расширения (доки,
  `.sh`, шаблоны); точные цифры по `*.py` — выше;
- разделение: **11 runtime-файлов** (app/core/modules) +
  `remote_edit.py`, `restore_server.py` (автономные/dev).

## 2. Перенесено в core (PHASE 2.0-2)

| Было (файл:строка) | Что делает | Стало |
|---|---|---|
| `system_routes._check_service` (681–682) | `systemctl is-active` + `is-enabled` служб в /about | `core.services.status()` |
| `system_routes.api_service_action` (1218) | `systemctl start/stop/restart` по роуту `/api/service/<svc>/<action>` | `core.services.control()` |
| `system_routes.api_disks` (2366–2386) | `lsblk` / `df -h` / `smartctl -a` для `/api/disks` | `core.storage.lsblk_text/df_text/smart_report()` |
| `capabilities._net_ifaces` | парсинг `/sys/class/net` (дубль из 1.1) | `core.network.physical_ifaces()` |
| `system_routes.load_settings` (40) | чтение `settings.json` + свой кэш | `core.config.load()` (единый кэш 10 с) |

Тесты: `tests/unit/test_core_layer.py` (+ route-контракты `/api/service/…`,
`/api/disks` — JSON-формы 1.1 сохранены байт-в-байт).

## 3. Остальные вызовы: план переноса

| Файл | Строк | Что делает | Целевое ядро | Фаза/статус |
|---|---|---|---|---|
| `app.py` | 91, 129 | `_cmd`-обёртка; `ping` проверки интернета | `core.process` | 2.0-3 |
| `core/discovery.py` | 152 | `run_scan` — хост-скан (ping/arp) | `core.process`; позже `core.network` | 2.0-3 / 2.0-6 |
| `core/module_loader.py` | 181 | dpkg-batch: неустановленные apt-пакеты | `core.process` | 2.0-3 |
| `core/samba_guest.py` | 159, 189 (+157) | `systemctl reload smbd` / `smbcontrol` | `core.services` | 2.0-3 |
| `modules/core_routes.py` | 79 | локальный `_cmd`-хелпер | `core.process.out` | 2.0-3 |
| `modules/inventory.py` | 14, 275, 289, 399 | `_cmd` + HTTP-скан устройств | `core.process` | 2.0-3 |
| `modules/media_routes.py` | 126, 226–309, 386–543, 635–821, 1228, 1380–1431 | статус IPTV (journalctl); Popen mpv/радио/плеер/будильник; curl UPnP/DLNA; `systemctl restart transmission` | journalctl → `core.services.logs`; curl → `core.process`; **Popen-проигрыватели — вне core** (длинноживущие процессы самой панели) | 2.0-3 (частично) |
| `modules/module_manager.py` | 48 (+85) | `_run`-обёртка (apt/git/install); `systemctl enable --now` | `core.process` / `core.services` | 2.0-3 |
| `modules/monitor.py` | 18 | `_cmd` | `core.process.out` | 2.0-3 |
| `modules/network_routes.py` | 62, 172–275 | bluetoothctl; nettools ping/dns/traceroute; `iw` wifi-scan | nettools → `core.process`; wifi-scan → `core.network` | 2.0-3 / 2.0-6 |
| `modules/system_routes.py` | 61 (хелпер), 700–836, 1045, 1555, 1644–1655 | статусы бэкапов/IPTV/служб/таймеров/health: `is-active`/`journalctl`/`list-timers` | `core.services` (`status`/`logs`) | 2.0-3 |
| `modules/system_routes.py` | 2055–2138 | запуск бэкапов/restore юнитов (`Popen start …`) | Jobs API (`core.jobs`) — запуск станет job'ом | 2.0-3 |
| `modules/system_routes.py` | 2294 | `do_clone` — клонирование диска (dd/ddrescue) | `core.storage` | 2.0-7 |
| `remote_edit.py` | 54 | dev-скрипт диагностики по ssh | — | **вне core** (только разработка) |
| `restore_server.py` | 130–245 | автономный recovery-сервер: systemctl юнитов, восстановление БД/eMMC | — | **вне core** (работает при убитой панели, без core/venv-зависимостей) |

## 4. Правила переноса

1. Один вызов (или одна обёртка) → один коммит с тестом; JSON-контракты
   роутов не меняются (аддитивность, §5).
2. Сначала обёртки (`_cmd`/`_run`), потом прямые вызовы внутри функций.
3. `Popen`-проигрыватели и recovery-скрипты не переносятся без
   отдельного обоснования (вне модели «одна команда = один вызов»).
4. После каждого пересчёта `rg` — обновлять таблицу и этот файл.

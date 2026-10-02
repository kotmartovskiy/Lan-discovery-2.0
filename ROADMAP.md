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
| **2.0-3** | Jobs subsystem | `core/jobs.py`: manager + worker-треды, состояния queued/running/completed/failed/cancelled, поля id/type/status/progress/started_at/finished_at/logs/result/error/cancelable; sqlite-таблица `jobs` (retention как у events); `GET /api/jobs` + `POST /api/jobs/<id>/cancel`; UI-виджет активных jobs; миграция на jobs: module install/update, backup/restore БД, network scan | pending |
| **2.0-4** | Module manifest 2.0 + permissions + trust | схема v2 (capabilities/dependencies/conflicts/services/configuration/role_support + publisher/sha256/min_core_version/max_core_version) — back-compat v1; сетка прав (network/storage/services/process/camera/usb/gpio/serial) в манифесте и UI-запрос при установке; catalog: SHA-256 тарболла из index, trusted sources, core-compat check; без PKI/sandbox (спека §11–12) | pending |
| **2.0-5** | Roles 2.0 | `roles/<id>.json` манифесты (required_modules/optional_modules/capabilities/hardware_requirements/dependencies/conflicts/recommended_configuration/security_profile); загрузчик в `core/roles.py`, compat через `compute_status()`/capabilities; роли-примеры: Network Gateway, Home Server, Remote Site, Industrial Gateway, Network Diagnostic Box, Camera Gateway (SDR — ждём хвост спеки); apply/API не ломаем (ALWAYS_ON сохраняется) | pending |
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

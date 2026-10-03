# CAPABILITIES — совместимость с hardware (спека §29, фаза 2.0-1)

Capabilities — **главный механизм hardware compatibility** (§35).
Hardware-снимок системы отвечает на «что здесь есть», а не «какая
плата». Код плато-специфики запрещён (§30).

## API

```text
GET /capabilities        # HTML
GET /api/capabilities    # JSON: {group: {...}, checked_at}
```

Источник: `core/capabilities.py::collect()` — 11 групп, TTL-кэши
(повторные запросы не перечитывают sysfs/тулы каждый раз).

## Группы

| Группа | Содержимое (источник) |
|---|---|
| `network` | интерфейсы, wifi/AP, loopback, адреса (sysfs/`/proc/net`) |
| `storage` | локальные/съёмные диски, SMART-поддержка (blkid/lsblk/`/sys/block`, smartctl) |
| `hardware` | SoC/борт (device-tree, `platform.machine`), USB-пары |
| `radio` | SDR, sub-GHz, RS-485 (глобы по /dev, тулы) |
| `camera` | USB-камеры (`/dev/video*`), IP-камеры (сетевые пробы) |
| `media` | ffmpeg/mpv, аудио-устройства (тулы, /dev/snd) |
| `service` | systemd-юниты, docker |
| `board` | модель платы (`detect_platform()`), hostname |
| `thermal` | thermal-зонды (`/sys/class/thermal`) |
| `tools` | nmap, traceroute, dnsutils, iw, bluez, smartmontools… |
| `checked_at` | время снимка |

## Семантика состояний

* значение capability: `present` / `absent` / `unknown`;
* надёжность: `measured` (измерено) / `detected` (обнаружено) /
  `unverified` (предположение) — флаги `reliability`;
* `unknown` — не пытались или не смогли проверить (честно, не `absent`).

## Потребители

* **UI**: страница `/capabilities` и бейджи в `/modules`;
* **манифесты модулей**: поле `capabilities: ["camera", ...]` — что
  модулю нужно; отсутствие → модуль помечен как несовместимый;
* **roles**: `role_blockers(rid)` — compat-check профиля по текущим
  capabilities (см. [ROLES.md](ROLES.md));
* **installer**: `tools/hw_detect.py` переиспользует
  `core.hardware.detect_platform()` (тот же источник, что `/api/health`);
* **portability**: инвариант §30 — capabilities вместо `if <board>`,
  доказано `tests/unit/test_portability.py`.

## Добавление нового hardware

1. Нет ветвлений по платам — определить capability (группа + probe);
2. probe — из device-tree/sysfs/тулов, состояние и reliability —
   по факту;
3. модуль декларирует требуемую группу в `module.json`;
4. тест: probe на заглушках + `collect()`-структура.

# INSTALL — установка LAN Discovery 2.0 (§33 Ph.10, Installer §25)

Чистая установка на **Debian / Ubuntu / Armbian** (ARM или x86_64).
Без предположений о плате: архитектура определяется в preflight,
hardware — capabilities (см. [PORTING.md](PORTING.md)).

## Требования

* root (sudo), Python ≥ 3.9 (`python3`), systemd, apt;
* диск ≥ ~400 MB свободно под префикс (venv + пакеты);
* сеть для apt и nmap-сканов (можно поставить `--skip-apt` на
  минимальной системе — критичные пакеты тогда должны быть уже).

## Установка

```bash
git clone <repo> lan-discovery && cd lan-discovery
sudo ./install.sh                 # полная цепочка §25
sudo ./install.sh --dry-run       # только показать план
sudo ./install.sh --skip-apt      # без apt (тестовое окружение)
sudo ./install.sh --no-enable     # не включать сервис сразу
```

## Что делает `install.sh` (цепочка §25)

| Шаг | Содержимое |
|---|---|
| preflight | `python3`/`python` ≥ 3.9, `uname -m/-r`, дистрибутив из `/etc/os-release` (WARN для не-Debian), apt/dpkg, место на диске |
| hardware | `tools/hw_detect.py` → `hw-detect.json` (тот же `core.hardware.detect_platform()`, что `/api/health`; stdlib — работает до venv) |
| dependencies | критичные обязательны (`python3-venv cffi cryptography bcrypt`), опциональные best-effort по одному (`nmap traceroute dnsutils iw bluez smartmontools ffmpeg mpv`) — неудачи пишутся в отчёт, установка не падает |
| core | код в `--prefix` (default `/opt/lan-discovery`), `venv --system-site-packages` (armhf без wheel), `pip install -r requirements.txt` |
| modules | `discover_modules()` — builtin-манифесты читаются |
| configuration | `/etc/lan-discovery/settings.json` (порт, подсеть), `users.json` (первый admin) |
| db | `init_db_schema()` — схема + миграции `PRAGMA user_version` |
| systemd | юнит `lan-discovery.service`, `enable --now` |
| health | python3-urllib: `GET /api/health` до 60 с, валидация JSON/`user_version` |

Идемпотентен: повторный запусок поверх существующей установки ничего
не ломает (код не перезаписывается, config/БД/юнит только дополняются).

## После установки

```bash
systemctl status lan-discovery
journalctl -u lan-discovery -f
# панель: http://<ip>:<port>/   (port из settings.web.flask_port, default 8080)
```

Проверка: `GET /api/health` (версия из `core/version.py`),
`GET /api/capabilities` (что hardware реально есть).

## Снос / откат установки

Чистая установка отката не требует — это просто каталог + юнит:

```bash
sudo systemctl disable --now lan-discovery
sudo rm -rf /opt/lan-discovery /etc/systemd/system/lan-discovery.service
sudo systemctl daemon-reload
# данные/конфиг при необходимости: /etc/lan-discovery, devices.db
```

Для уже работающей системы смотри [UPGRADE.md](UPGRADE.md)
(update.sh — бэкап + авто-откат).

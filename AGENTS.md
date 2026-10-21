# AGENTS.md — Инструкции для AI-ассистента

## Обязательные правила

### БЭКАПЫ ПЕРЕД ЛЮБЫМ ИЗМЕНЕНИЕМ
**ПЕРЕД КАЖДЫМ РЕДАКТИРОВАНИЕМ app.py (или любого другого файла):**
1. Сделать бэкап через SSH:
   ```
   cp /opt/lan-discovery/app.py /opt/lan-discovery/app.py.backup-$(date +%Y%m%d-%H%M%S)
   ```
2. Убедиться что бэкап создан успешно
3. Только потом делать изменение

Это правило НЕОБХОДИМО соблюдать всегда без исключений.

### ГИТХАБ: ОБЯЗАТЕЛЬНО ВНОСИТЬ ИЗМЕНЕНИЯ В ПРОЕКТ И ДОКУМЕНТАЦИЮ
**Любые дополнения и изменения кода, шаблонов и документации нужно коммитить и пушить на GitHub — иначе репозиторий отстаёт от работающей системы.**

Репозитории:
- **`Lan-discovery-2.0`** (приватный) — https://github.com/kotmartovskiy/Lan-discovery-2.0 — **ОСНОВНОЙ репозиторий ЭТОЙ рабочей копии** (`C:\Users\Lenovo\Documents\Lan-discovery-2.0`, версия 2.0 — Universal Modular Appliance Platform). Код, `docs/` (`Спецификация-2.0.md`, `Архитектура-2.0.md`, `ROADMAP-1.1.md`), `README.md`, `AGENTS.md`, `ROADMAP.md`. Ветка `main`, локальный `origin` указывает сюда; коммит/пуш — сюда (`git push origin main`). Фазы 2.0 — в `ROADMAP.md` этого репо.
- **`Lan-discovery-1.1`** (приватный) — https://github.com/kotmartovskiy/Lan-discovery-1.1 — **версия 1.1, ЗАМОРОЖЕНА** (21.10.2026 X96 переведён на 2.0 и 1.1 с него удалена). Рабочая копия `C:\Users\Lenovo\Documents\Default Project` — только горячие фиксы 1.1 по отдельному указанию. В репо 2.0 этот remote назван **`upstream-11`** — оттуда cherry-pick'аются фиксы.
- **`Lan-discovery-ARM`** (приватный) — https://github.com/kotmartovskiy/Lan-discovery-ARM — **версия 1.0, ЗАМОРОЖЕНА** (21.10.2026 Orange Pi переведена на 2.0, 1.0 удалена). Пушить только горячие фиксы 1.0 по отдельному указанию.
- **`Lan-discovery-docs`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-docs — очищенная от рабочих IP/паролей копия `docs/`.
- **`Lan-discovery-modules`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-modules — каталог модулей для вкладки «Модули» (`index.json` + `<id>/` с `module.json`); панель ставит/обновляет модули из этой ветки (настройка `modules_catalog` в `/etc/lan-discovery/settings.json`). Схема манифеста: v1 сейчас, **v2 — PHASE 2.0-4** (после чего каталог пополнится publisher/sha256-полями).
- **`Lan-discovery-demo`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-demo — **статичный демо-слепок панели** для показа извне, сайт: https://kotmartovskiy.github.io/Lan-discovery-demo/ (GitHub Pages, ветка `main`, корень). Генератор `tools/make_demo.py`, проверка `tools/demo_lint.py`. Слепок снимается с **боевой 2.0 на X96**.

Порядок действий:
1. `git status` → `git add <изменённые файлы>` (мусор типа `check_*`, `debug*`, `verify*`, `__pycache__` отфильтровывается `.gitignore`).
2. Коммит — сообщение по-русски с префиксом: `core:` `ui:` `docs:` `fix:` `tools:` `weather:` `chore:`.
3. `git push origin main`.
4. Если менялся `docs/` — обновить публичную версию:
   ```
   python tools/sanitize_docs.py        # выход 0 = утечек нет
   cd <temp>/docs-public && git add -A && git commit -m "docs: ..." && git push
   ```
5. Если правки вносились на сервер (`/opt/lan-discovery/*`) — сначала подтянуть их в локальную копию (SFTP read → правка → commit), чтобы репозиторий соответствовал проду.
6. Обновление демо-сайта (по запросу):
   ```
   # на боксе: cd /opt/lan-discovery && LAN_PANEL_PASS=<пароль админа> venv/bin/python tools/make_demo.py /tmp/demo
   # скачать /tmp/demo локально (pscp -r), затем:
   python -X utf8 tools/demo_lint.py <папка-demo>    # exit 0 = чисто
   # скопировать в клон Lan-discovery-demo → add -A → commit → push (Pages пересобирается сам)
   ```
7. Проверка дрейфа «репозиторий ↔ сервер» (после серии правок / по требованию):
   ```
   $env:LAN_SSH_PASS='<ssh-пароль>'; python -X utf8 tools/sync_check.py
   # exit 0 = синхронно; 1 = content diff / не залито / лишнее на сервере; 2 = SSH-сбой
   # без SSH: --filelist <файл> (генератор — /tmp/filelist_host.py на боксе)
   # deploy-инвентарь и docs по умолчанию не сверяются (см. SKIP_LOCAL в скрипте)
   ```
   Автозапуск (№67): ежедневная Windows-задача `LanDiscovery-SyncCheck`
   в 09:30 (регистрация: `tools\setup_sync_task.ps1`; лог:
   `%LOCALAPPDATA%\lan-discovery\sync_check.log`; SSH-пароль хранится
   только в `%LOCALAPPDATA%\lan-discovery\ssh_pass.txt`, в git не идёт).
   **Важно:** задача №67 настраивалась под пару «`Default Project` ↔ X96» (1.1) —
   после перехода X96 на 2.0 (21.10.2026) перенастроить на «клон 2.0 ↔ X96»
   либо отключить по указанию.

## Рабочее окружение
- **ЭТА копия — версия 2.0 (ОСНОВНАЯ, в разработке):**
  `C:\Users\Lenovo\Documents\Lan-discovery-2.0`.
  **С 21.10.2026 обе боевые системы работают на 2.0** в стандартном префиксе
  `/opt/lan-discovery` на порту `:8080`; версии 1.1 (X96) и 1.0 (Orange Pi)
  с боксов удалены (бэкапы — ниже), репозитории 1.1/ARM заморожены.
  Деплой — только по отдельному указанию.
- **X96 Max — боевая 2.0** (`192.168.3.243:8080`, hostname `armbian`):
  код `/opt/lan-discovery` (venv `/opt/lan-discovery/venv`, systemd
  `lan-discovery` enabled); конфиг `/etc/lan-discovery` (общий для скриптов).
  Бэкапы при переезде 21.10.2026: `/root/backup-1.1-remove-20261021-140415/`
  (тар 1.1 + конфигов + юнитов), `/etc/lan-discovery.backup-pre20-*`.
- **Orange Pi — боевая 2.0** (`192.168.3.234` (LAN) / `192.168.3.235` (WiFi AP),
  hostname `orangepiplus`): код `/opt/lan-discovery`, панель `:8080`,
  **БД живёт на HDD** — симлинк `/opt/lan-discovery/devices.db ->
  /srv/lan-discovery/devices.db` + drop-in `Requires=srv.mount` (без HDD
  панель не стартует — это дизайн). Бэкап при переезде 21.10.2026:
  `/root/backup-1.0-remove-20261021-142820/` (тар юнитов/скриптов, копия
  devices.db, каталог 1.0 целиком — `opt-lan-discovery-1.0`).
  **Доступ:** LAN `.234` снова отвечает (SSH-баннер жив); если на `.235`
  SSH-баннер пустой/обрывается — работать через `.234`. SSH root/1234.
  git на OP нет — репо заливается архивом (SFTP `xfer.py`).
- **Обе панели активны и сканируют сеть** — двойные сканы допустимы;
  при необходимости скан выключается в настройках одной из панелей.
- Web panel: `http://192.168.3.243:8080` (X96), `http://192.168.3.234:8080` (OP)
- Thinkpad T480: IP `192.168.3.236` (WiFi) / `192.168.3.239` (LAN)
- Flask app: `/opt/lan-discovery/app.py` (на боксе — внутри venv)
- Systemd service: `lan-discovery`; таймеры: `weather-update.timer` (15 мин),
  `env-data.timer` (4 ч), `backup-db.timer`, `update-iptv.timer`
  (скрипты погоды/env — `/usr/local/sbin/weather-update.py` и
  `/usr/local/sbin/env_data.py` — на ОБЕИХ боксах это версии из `deploy/` 2.0)
- **SSH-хелперы (локально, `%TEMP%\opencode\`):**
  `rr.py <script.sh> <host> [timeout-sec]` — выполнение bash-скрипта на боксе
  (по умолчанию host `.235`, timeout 600); `xfer.py put|get <host> <local>
  <remote>` — SFTP-перенос файлов (пароль в скрипте).

## Процесс редактирования app.py
1. SFTP read → Python string replace → SFTP write → syntax check → `systemctl restart lan-discovery`
2. Синтакс-проверка: `python3 -c "import py_compile; py_compile.compile('/opt/lan-discovery/app.py', doraise=True)"`
3. Рестарт: `systemctl restart lan-discovery`
4. venv-пакеты: `/opt/lan-discovery/venv/bin/pip install -r requirements.txt`

## Сетевые устройства
- Known web ports: `{"192.168.3.234": 8080, "192.168.3.235": 8080, "192.168.3.51": 8080}`
- Server IPs to skip web probing: `{"192.168.3.234", ".235", ".51"}`
- Orange Pi: `192.168.3.234` (LAN), `192.168.3.235` (WiFi AP)
- Disk: eMMC `/dev/mmcblk2` (14.6G), HDD `/dev/sda` (232.9G), SD `/dev/mmcblk0` (29.1G)

## Форматы
- Дата/время: DD.MM.YYYY HH:MM:SS
- Сетевые проверки: `network_check.py` + HTTP fallback
- Погода: координаты 57.0, 41.0, timezone Europe/Moscow; **источник — met.no**
  (основной: `api.open-meteo.com` с боксов недоступен — 429/таймаут), прогноз
  + backfill прошедших часов дня — `historical-forecast-api.open-meteo.com`
  (скрипт `deploy/weather-update.py`, привязан к `weather-update.timer`)

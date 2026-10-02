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
- **`Lan-discovery-1.1`** (приватный) — https://github.com/kotmartovskiy/Lan-discovery-1.1 — **версия 1.1, продолжает жить отдельно** (боевая на X96/OP; рабочая копия `C:\Users\Lenovo\Documents\Default Project`, там свой origin). В репо 2.0 этот remote назван **`upstream-11`** — оттуда cherry-pick'аются горячие фиксы безопасности 1.1 в 2.0 и наоборот. Коммиты в 1.1 делаются в `Default Project`.
- **`Lan-discovery-ARM`** (приватный) — https://github.com/kotmartovskiy/Lan-discovery-ARM — **версия 1.0, ЗАМОРОЖЕНА** (Orange Pi работает на этой версии; локальный remote `origin-1.0` в `Default Project`). Пушить только горячие фиксы 1.0 по отдельному указанию.
- **`Lan-discovery-docs`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-docs — очищенная от рабочих IP/паролей копия `docs/` (пока — документация 1.1; для 2.0 публичную доку выделим отдельно по указанию).
- **`Lan-discovery-modules`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-modules — каталог модулей для вкладки «Модули» (`index.json` + `<id>/` с `module.json`); панель ставит/обновляет модули из этой ветки (настройка `modules_catalog` в `/etc/lan-discovery/settings.json`). Схема манифеста: v1 сейчас, **v2 — PHASE 2.0-4** (после чего каталог пополнится publisher/sha256-полями).
- **`Lan-discovery-demo`** (публичный) — https://github.com/kotmartovskiy/Lan-discovery-demo — **статичный демо-слепок панели** для показа извне, сайт: https://kotmartovskiy.github.io/Lan-discovery-demo/ (GitHub Pages, ветка `main`, корень). Генератор `tools/make_demo.py`, проверка `tools/demo_lint.py`. Слепок снимается с **боевой 1.1 на X96**.

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

## Рабочее окружение
- **ЭТА копия — версия 2.0 (в разработке):** `C:\Users\Lenovo\Documents\Lan-discovery-2.0`.
  **2.0 НЕ деплоится на X96 и Orange Pi** — обе боевые системы на 1.1;
  деплой 2.0 будет только на отдельный префикс/устройство и ТОЛЬКО по
  отдельному указанию. `tools/sync_check.py` и ежедневная задача №67
  относятся к паре «`Default Project` ↔ X96» (1.1), для 2.0 — настраиваются
  позже, когда появится деплой-цель.
- **ТЕСТОВАЯ СИСТЕМА версии 1.1 — X96 Max** (`192.168.3.243:8080`, hostname `armbian`):
  на неё деплоим всё новое из `Lan-discovery-1.1` (рабочая копия `Default Project`), здесь проверяем этапы 1.1; также источник cherry-pick-фиксов в 2.0.
- **Orange Pi (`192.168.3.235`) — рабочий инструмент на версии 1.0**:
  **изменения 1.1 на OP НЕ деплоим**, она остаётся на текущей версии (`Lan-discovery-ARM`).
- **Web panel: `http://192.168.3.243:8080`** (X96 Max / Armbian, hostname `armbian`)
  — lan-discovery **переехал сюда** 28.09.2026 (venv: `/opt/lan-discovery/venv`,
  systemd `lan-discovery` enabled, данные `devices.db` перенесены)
- X96 Max: IP `192.168.3.243`, SSH root/1234 (hostkey `SHA256:TpeCxMP+...`)
- Orange Pi: `192.168.3.234` (LAN) **не отвечает** (кабель/интерфейс отвал),
  доступен через WiFi AP `192.168.3.235` (hostkey `SHA256:fwnWkNuM+...`);
  сервис `lan-discovery` на OP ещё активен — при необходимости остановить
  (`systemctl disable --now lan-discovery`), чтобы не сканировали сеть дважды
- Thinkpad T480: IP `192.168.3.236` (WiFi) / `192.168.3.239` (LAN)
- Flask app: `/opt/lan-discovery/app.py` (на боксе — внутри venv)
- Systemd service: `lan-discovery`

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
- Погода: Open-Meteo (57.0, 41.0), timezone Europe/Moscow

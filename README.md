# Lan-discovery 2.0.0

Веб-панель управления домашней сетью для одноплатных компьютеров (Orange Pi, X96 Max / Amlogic, generic Debian/ARM): обнаружение устройств, мониторинг, IPTV/радио/плеер, сетевые инструменты, файлы, заметки, бэкапы и клонирование eMMC → SD.

> **Статус 2.0.0 (03.10.2026):** Universal Modular Appliance Platform —
> спека §0–37 выполнена, ROADMAP 2.0-0…2.0-21 закрыт (спека —
> `docs/Спецификация-2.0.md`, архитектура — `docs/Архитектура-2.0.md`,
> guides — `docs/2.0/`). **Боевые системы (X96, Orange Pi) работают на
> версии 1.1** — репозиторий `Lan-discovery-1.1`; 2.0 на них не
> деплоится.

- **Панель:** `http://<ip>:8080` (Flask, Python 3, venv)
- **Сервис:** `systemctl status lan-discovery`, код — `/opt/lan-discovery/app.py`

## Лицензия

**Проприетарная** — см. [LICENSE](LICENSE). Личное некоммерческое
использование свободно; коммерческое использование, распространение
и публичные копии кода — только по письменному разрешению
правообладателя (kotmartovskiy).

## Что нового в версии 2.0 (03.10.2026)

**Universal Modular Appliance Platform** (журнал — [CHANGELOG.md](CHANGELOG.md),
release notes — [docs/2.0/RELEASE_NOTES.md](docs/2.0/RELEASE_NOTES.md)):

- **Core-подсистемы с контрактами**: jobs (долгие операции с прогрессом и
  событиями), network (+транзакции prepare/apply/verify/rollback), storage,
  events, automation (правила Event→Action), roles (декларативные профили),
  capabilities (11 групп), secrets (Fernet), единый источник версии (§34);
  инверсии слоёв устранены: `modules → app` = 0.
- **Портируемость (§30)**: плато-специфика убрана из кода — Orange Pi /
  X96 Max / x86_64 через capabilities (grep-тест в CI); Installer §25
  (`install.sh --dry-run`, best-effort зависимости); appliance smoke §27.
- **Надёжность**: manifest trust (sha256/publishers), system rollback §26
  (`core/syschange`), device identity (mac/ip, БД v3).
- **UI 2.0**: Application Shell (разделы HOME…ADMIN), dashboard на главной,
  `/devices`, `/storage`, единые toast/confirm (§23), demo mode §28
  (API adapter → fixtures).
- **Документация**: 11 guides §29 + INSTALL/UPGRADE/MIGRATION — `docs/2.0/`.

## Что нового в версии 1.1 (01.10.2026)

Версия 1.1 — **UI/UX-редизайн + слой Hardware → Capabilities → Modules → Roles**
(полный журнал — [docs/ROADMAP-1.1.md](docs/ROADMAP-1.1.md) §9, аудит — [UI_UX_AUDIT.md](UI_UX_AUDIT.md)):

- **UI/UX**: единый design system (`static/style.css`), доменные группы навигации,
  сводная панель `GET /api/dashboard` (один запрос вместо ×2), новые таблицы
  устройств/истории, semantic-статусы мониторинга, responsive (≤700/≤420 px),
  базовая доступность (skip-link, семантика, focus-visible, `scope="col"`).
- **Capabilities (STEP 7)**: `GET /capabilities` + `GET /api/capabilities` —
  hardware-снимок с `reliability`-флагами.
- **Статусы модулей (STEP 8)**: один semantic-бейдж (`compute_status`),
  поля `version/source/permissions/hardware` в `module.json`, apt-зависимости
  одним dpkg-батчем.
- **Роли (STEP 9)**: профили `default/media/network`, всегда-включённые модули,
  compat-check при применении — `GET /roles` + `/api/roles`.
- **Cleanup (STEP 12)**: удалены мёртвые шаблоны/CSS, возвращены кнопки бэкапов,
  справка API → `docs/API.md`.

## Документация

Полные инструкции — в [`docs/`](docs/) (они же — [wiki](https://github.com/kotmartovskiy/Lan-discovery-ARM/wiki)):

| Страница | О чём |
|---|---|
| [Home](docs/Home.md) | Обзор возможностей |
| [Установка](docs/Установка.md) | `install.sh`, зависимости, systemd, таймеры, деплой |
| [Обновление](docs/Обновление.md) | `update.sh`: бэкап → применение → авто-откат |
| [Восстановление](docs/Восстановление.md) | `recovery.sh`: restore кода/конфига/БД из бэкапов |
| [Архитектура](docs/Архитектура.md) | Структура кода, core/, данные, lifecycle-скрипты, тесты |
| [Модули](docs/Модули.md) | Ответственность модулей и роутов |
| [API](docs/API.md) | Каталог всех 172 роутов: метод/путь/доступ |
| [Конфигурация](docs/Конфигурация.md) | settings.json, переопределения, события/retention |
| [Безопасность](docs/Безопасность.md) | Роли, CSRF, секреты, периметр, ограничения |
| [IPTV](docs/IPTV.md) | Плейлисты, таймер 04:15, диагностика |
| [SD-клонирование](docs/SD-клонирование.md) | Копирование eMMC → SD, API, осторожности |
| [Погода](docs/Погода.md) | Open-Meteo, таймеры, weather-monitor |
| [Полезные команды](docs/Полезные-команды.md) | SSH, логи, бэкапы, типовые неполадки |

Публичная (очищенная от рабочих адресов и паролей) версия документации: **https://github.com/kotmartovskiy/Lan-discovery-docs**

## Стек

Python 3 · Flask 3 · Flask-SocketIO · Flask-WTF · bcrypt · SQLite · Jinja2 · systemd · pytest + CI

## Быстрый старт

```bash
# чистая установка (идемпотентна, есть --dry-run):
sudo ./install.sh

# обновление (бэкап + авто-откат при сбое):
sudo ./update.sh

# восстановление из бэкапов:
sudo ./recovery.sh --dry-run
```

Ручная альтернатива и регламент правок на сервере — **бэкап → правка → `py_compile` → `systemctl restart lan-discovery`** (см. [Установка](docs/Установка.md)).

Тесты: `pip install -r requirements-dev.txt && pytest` (unit — без сети, live-маркер `live` требует панель); CI гоняет unit + `py_compile` на каждый push.

## Структура репозитория

```
app.py            точка входа: Flask/CSRF, регистрация модулей, фон, main
core/             hardware (платформа/температура/диски), discovery (скан),
                  events (журнал+retention), module_loader, module_catalog,
                  capabilities (1.1: hardware-снимок), roles (1.1: профили
                  модулей), dashboard (1.1: агрегат для шапки)
modules/          роуты и логика (auth, devices, system, network, media,
                  weather, monitoring, inventory, core) + модули-компоненты
templates/        Jinja2-шаблоны (base.html — каркас панели)
static/           design system style.css (1.1)
games/, static/   игры и статика
tests/            pytest: unit (без сети) + live (маркер live)
install.sh        чистая установка     update.sh   обновление с откатом
recovery.sh       restore из бэкапов   deploy/     systemd-юниты и шаблоны
docs/             документация         tools/      sanitize/demo-скрипты
.github/          CI (pytest unit + py_compile)
deploy.py         деплой на сервер: бэкап → SFTP → проверка → рестарт
ROADMAP.md        план/журнал версий   UI_UX_AUDIT.md  аудит UI/UX (1.1)
CHANGELOG.md      семвер-чейнджлог релизов (Keep a Changelog)
```

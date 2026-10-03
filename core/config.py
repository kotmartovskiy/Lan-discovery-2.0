# -*- coding: utf-8 -*-
"""Settings layer (PHASE 2.0-2 + 2.0-14): settings.json без app (§20).

Проблема 1.1: путь SETTINGS_PATH и чтение settings продублированы в
app.py, core/module_catalog.py, modules/system_routes.py,
modules/weather_routes.py (см. docs/Инвентаризация-core-2.0.md) — здесь
единый источник; потребители переезжают постепенно.

Классы конфигов (спека §20) — единый справочник путей, «не всё в один
файл»:

- Core configuration -> SETTINGS_PATH (settings.json, этот модуль)
- Secrets            -> core.secrets (SECRETS_DIR/kv.json, secret.key; §21)
- Module configuration -> module.json манифесты (repo) + секции settings
- Role configuration -> core.roles (roles/*.json + ROLES_STATE_PATH)
- Runtime state      -> MODULES_STATE_PATH (modules.json), БД devices/jobs

Контракт:
    SETTINGS_PATH            — путь по умолчанию (/etc/lan-discovery/...)
    PREFIX, DB_PATH          — префикс установки и БД рядом с кодом (§25)
    load(path) -> dict       — кэш 10 с на путь; битый/нет файла → {}
    get(section, key, default, path)
    save(data, path)         — атомарно (write_json_atomic), пишет кэш
    write_json_atomic(p, d)  — ЕДИНЫЙ механизм записи JSON state (§20):
                               tmp → flush+fsync → os.replace; сбой не
                               трогает цель (читатель видит старый или
                               новый валидный JSON, не обрезанный)
    clear_cache(path)        — сброс кэша (для тестов/после правок)
"""
import json
import os
import time

SETTINGS_PATH = "/etc/lan-discovery/settings.json"
# runtime state (§20): владельцы — module_loader/roles (алиасы там)
MODULES_STATE_PATH = "/etc/lan-discovery/modules.json"
ROLES_STATE_PATH = "/etc/lan-discovery/roles.json"


def _default_prefix():
    """Префикс установки: env LAN_PREFIX, иначе каталог самого кода.

    core/config.py лежит в <prefix>/core/ → dirname(dirname(__file__))
    == префикс: default-установка даёт /opt/lan-discovery (как и раньше),
    `./install.sh --prefix DIR` — свой каталог; в репо-чекауте — корень
    репо (*.db в .gitignore). Env LAN_PREFIX — явный override (юнит/шелл).
    """
    return os.environ.get("LAN_PREFIX") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))


PREFIX = _default_prefix()
DB_PATH = os.path.join(PREFIX, "devices.db")

_TTL = 10
_cache = {}  # путь -> {"data": dict, "ts": float}


def load(path=None):
    """Настройки dict; недоступны/не JSON → {}; кэш 10 с на путь."""
    path = path or SETTINGS_PATH
    now = time.time()
    c = _cache.get(path)
    if c is not None and now - c["ts"] < _TTL:
        return c["data"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        # ошибку не кэшируем — файл может появиться/починиться
        return {}
    _cache[path] = {"data": data, "ts": now}
    return data


def get(section, key, default=None, path=None):
    """Значение секция/ключ или default (стиль _cfg)."""
    s = load(path)
    sec = s.get(section)
    if not isinstance(sec, dict):
        return default
    return sec.get(key, default)


def write_json_atomic(path, data):
    """Атомарная запись JSON: tmp → flush+fsync → os.replace.

    Сбой сериализации/записи удаляет tmp и НЕ трогает цель — убийство
    процесса в любой точке оставляет старый валидный файл либо новый
    валидный файл, но никогда обрезанный JSON. Исключение пробрасывается
    — обёртки (save_state/_write_state/save) возвращают False.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return True


def save(data, path=None):
    """Атомарная запись + инвалидация кэша. True при успехе."""
    path = path or SETTINGS_PATH
    write_json_atomic(path, data)
    _cache[path] = {"data": data, "ts": time.time()}
    return True


def clear_cache(path=None):
    """Сброс кэша: один путь или весь."""
    if path is None:
        _cache.clear()
    else:
        _cache.pop(path, None)

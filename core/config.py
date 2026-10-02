# -*- coding: utf-8 -*-
"""Settings layer (PHASE 2.0-2): settings.json без зависимости от app.

Проблема 1.1: путь SETTINGS_PATH и чтение settings продублированы в
app.py, core/module_catalog.py, modules/system_routes.py,
modules/weather_routes.py (см. docs/Инвентаризация-core-2.0.md) — здесь
единый источник; потребители переезжают постепенно.

Контракт:
    SETTINGS_PATH            — путь по умолчанию (/etc/lan-discovery/...)
    load(path) -> dict       — кэш 10 с на путь; битый/нет файла → {}
    get(section, key, default, path)
    save(data, path)         — атомарно (tmp + os.replace), пишет кэш
    clear_cache(path)        — сброс кэша (для тестов/после правок)
"""
import json
import os
import time

SETTINGS_PATH = "/etc/lan-discovery/settings.json"
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


def save(data, path=None):
    """Атомарная запись + инвалидация кэша. True при успехе."""
    path = path or SETTINGS_PATH
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    _cache[path] = {"data": data, "ts": time.time()}
    return True


def clear_cache(path=None):
    """Сброс кэша: один путь или весь."""
    if path is None:
        _cache.clear()
    else:
        _cache.pop(path, None)

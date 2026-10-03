# -*- coding: utf-8 -*-
"""Demo mode (спека §28, PHASE 2.0-21) — API adapter панели.

Архитектура §28:

    UI (production-шаблоны)
      ↓  GET /api/*
    API adapter (core/demo — before_request в app.py)
      ↙            ↘
    Real API      Demo API → fixtures (<dir>/api/<path>.json)

Режим включается ``settings.web.demo = true`` или переменной
``LAN_DEMO=1``; каталог фикстур — ``settings.web.demo_dir`` или
``LAN_DEMO_DIR`` (default ``/etc/lan-discovery/demo``).

В demo-режиме:
* GET/HEAD ``/api/*`` → фикстура (секреты вычищены заранее);
* без фикстуры → безопасный ``{ok:false, demo:true}`` 404 — UI
  показывает empty/error state (§23), не падает;
* изменяющие методы (POST/PUT/DELETE/PATCH) → 403 — demo read-only;
* HTML не копируется: страницы рендерятся production-шаблонами
  (плашка «Демо-режим» — контекст ``demo_mode``).

Фикстуры готовит ``tools/make_demo.py --fixtures <dir>`` (снимок
GET-API с вычисткой секретов, MAC и имён — те же санитайзеры, что
в статичном демо-сайте).
"""
import json
import os
import threading
import time

_NO_FIXTURE = object()

DEFAULT_FIXTURES_DIR = "/etc/lan-discovery/demo"


def demo_enabled():
    """Demo-режим включён? (env LAN_DEMO=1 или settings.web.demo)."""
    v = os.environ.get("LAN_DEMO")
    if v is not None:
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    try:
        from core.config import get
        return bool(get("web", "demo", False))
    except Exception:
        return False


def fixtures_dir():
    """Каталог фикстур: env LAN_DEMO_DIR → settings.web.demo_dir → default."""
    d = os.environ.get("LAN_DEMO_DIR")
    if d:
        return d
    try:
        from core.config import get
        d = get("web", "demo_dir", "") or ""
        if d:
            return str(d)
    except Exception:
        pass
    return DEFAULT_FIXTURES_DIR


_lock = threading.Lock()
_cache = {"dir": None, "ts": 0.0, "data": {}}
_TTL = 5.0


def _load(dirpath):
    """{путь: данные} из <dir>/api/*.json (кэш 5 с, битые файлы — пропуск)."""
    data = {}
    api_dir = os.path.join(dirpath, "api")
    if os.path.isdir(api_dir):
        for fn in sorted(os.listdir(api_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(api_dir, fn),
                          encoding="utf-8") as f:
                    data["/api/" + fn[:-len(".json")]] = json.load(f)
            except (OSError, ValueError):
                continue
    return data


def fixtures(force=False):
    """Кэшированные фикстуры для текущего каталога."""
    dirpath = fixtures_dir()
    now = time.time()
    with _lock:
        if (not force and _cache["dir"] == dirpath
                and now - _cache["ts"] < _TTL):
            return _cache["data"]
        data = _load(dirpath)
        _cache.update(dir=dirpath, ts=now, data=data)
        return data


def fixture(path):
    """Данные фикстуры для GET-пути или ``_NO_FIXTURE``."""
    key = path.split("?", 1)[0].rstrip("/") or "/"
    return fixtures().get(key, _NO_FIXTURE)


def clear_cache():
    """Сброс кэша (тесты/после regen фикстур)."""
    with _lock:
        _cache.update(dir=None, ts=0.0, data={})

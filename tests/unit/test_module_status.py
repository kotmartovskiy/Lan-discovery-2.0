# -*- coding: utf-8 -*-
"""Unit: вычисляемые статусы модулей (STEP 8) — compute_status + /modules."""
import time

import pytest

import app
import modules.auth as auth
from core.module_loader import MODULE_STATUSES, compute_status, modules_with_status


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c


CTX = {
    "arch": "aarch64",
    "caps": {
        "tools": {"nmap": {"state": "present"}, "gpu-tool": {"state": "absent"}},
        "storage": {"emmc": {"state": "present"}, "hdd": {"state": "absent"}},
    },
}


def test_unknown_broken_manifest():
    assert compute_status(None, {}, CTX) == "unknown"
    assert compute_status({}, {}, CTX) == "unknown"


def test_incompatible_arch():
    m = {"id": "x", "hardware": {"arch": ["x86_64"]}}
    assert compute_status(m, {}, CTX) == "incompatible"
    m_ok = {"id": "x", "hardware": {"arch": ["aarch64"]}}
    assert compute_status(m_ok, {}, CTX) == "available"


def test_requires_hardware():
    m_tool = {"id": "x", "hardware": {"tools": ["gpu-tool"]}}
    assert compute_status(m_tool, {}, CTX) == "requires-hardware"
    m_st = {"id": "x", "hardware": {"storage": ["hdd"]}}
    assert compute_status(m_st, {}, CTX) == "requires-hardware"
    # выполненные требования не мешают
    m_ok = {"id": "x", "hardware": {"tools": ["nmap"], "storage": ["emmc"]}}
    assert compute_status(m_ok, {}, CTX) == "available"


def test_requires_dependency_package():
    m = {"id": "x", "deps": {"apt": ["nope-pkg"]}}
    assert compute_status(m, {}, CTX,
                          missing_pkgs={"nope-pkg"}) == "requires-dependency"
    # отсутствующий пакет чужого модуля не влияет
    assert compute_status(m, {}, CTX,
                          missing_pkgs={"other-pkg"}) == "available"


def test_requires_dependency_dir(tmp_path):
    m = {"id": "x", "deps": {"dirs": ["/definitely/not/existing/dir"]}}
    assert compute_status(m, {}, CTX) == "requires-dependency"
    m_ok = {"id": "x", "deps": {"dirs": [str(tmp_path)]}}
    assert compute_status(m_ok, {}, CTX) == "available"


def test_error_last_install_failed():
    m = {"id": "x", "builtin": True}
    entry = {"installed": True, "enabled": True, "last": {"ok": False}}
    assert compute_status(m, entry, CTX) == "error"


def test_disabled_active_available():
    m = {"id": "x", "builtin": True}
    assert compute_status(
        m, {"installed": True, "enabled": False}, CTX) == "disabled"
    assert compute_status(
        m, {"installed": True, "enabled": True}, CTX) == "active"
    # builtin без записи состояния → active (дефолт True/True)
    assert compute_status(m, {}, CTX) == "active"
    # небuiltin без записи → available
    assert compute_status({"id": "y"}, {}, CTX) == "available"


def test_priority_incompatible_over_error():
    m = {"id": "x", "hardware": {"arch": ["x86_64"]}}
    entry = {"last": {"ok": False}}
    assert compute_status(m, entry, CTX) == "incompatible"


def test_modules_with_status_shape():
    rows = modules_with_status()
    assert rows, "модули не найдены"
    for row in rows:
        assert row["status"] in MODULE_STATUSES, row["m"].get("id")
        assert row["m"].get("id")


def test_modules_page_renders(client):
    r = client.get("/modules")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Модули" in html
    # вычисляемые статусы вместо старых бейджей
    assert ("● активен" in html) or ("доступен" in html) or ("выключен" in html)
    assert ".badge-warn" in html  # semantic-стиль статусов требований
    # манифесты v2 несут version (PHASE 2.0-15) → «Unknown» не показывается
    assert ">Unknown<" not in html
    assert ">v2.0.0<" in html


# --- Task2: атомарное состояние модулей (modules.json) ----------------------

def test_save_state_atomic_and_corrupt(tmp_path, monkeypatch):
    """Убийство в середине записи не оставляет обрезанный modules.json."""
    import json
    import os
    import core.module_loader as ml

    p = str(tmp_path / "modules.json")
    monkeypatch.setattr(ml, "STATE_PATH", p)
    monkeypatch.setattr(ml, "_state_cache", {"mtime": -1, "data": None})

    # успех: валидный JSON без tmp-обломков, кэш инвалидирован
    assert ml.save_state({"order": ["a", "b"], "statuses": {}}) is True
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"order": ["a", "b"], "statuses": {}}
    assert not os.path.exists(p + ".tmp")
    assert ml.load_state() == {"order": ["a", "b"], "statuses": {}}

    # неатомарный провал: цель цела, tmp убран, False наверх
    class _NotJson:
        pass

    assert ml.save_state({"order": _NotJson()}) is False
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"order": ["a", "b"], "statuses": {}}
    assert not os.path.exists(p + ".tmp")

    # битый (рваный) файл => читатель видит default ({}), не исключение
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"order": [')
    monkeypatch.setattr(ml, "_state_cache", {"mtime": -1, "data": None})
    assert ml.load_state() == {}

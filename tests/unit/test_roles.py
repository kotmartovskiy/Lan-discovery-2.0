# -*- coding: utf-8 -*-
"""Unit: roles layer 2.0 — манифесты roles/*.json, apply, API /api/roles."""
import json
import os
import time

import pytest

import app
import core.roles as roles
import modules.auth as auth
from core.module_loader import MODULE_STATUSES, discover_modules, nav_groups


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = old


def _reset_cache(monkeypatch):
    monkeypatch.setattr(roles, "_roles_cache", {"ts": 0.0, "data": None})


def test_role_files_on_disk_valid():
    assert roles.load_roles(), "роли не загрузились"
    for rid, m in roles.load_roles().items():
        assert roles.validate_role(m, rid) == [], rid
    # legacy-профили 1.1 сохранены + 6 ролей из спеки §14 (SDR ждём)
    assert {"default", "media", "network"} <= set(roles.load_roles())
    assert {"network-gateway", "home-server", "remote-site",
            "industrial-gateway", "network-diagnostic-box",
            "camera-gateway"} <= set(roles.load_roles())
    assert "sdr" not in roles.load_roles()  # хвост спеки ждём


def test_profiles_reference_real_modules():
    # legacy-профили обязаны ссылаться только на существующие модули;
    # новые спека-роли могут ссылаться на будущие — это честно фиксирует
    # roles_overview() в поле missing
    known = {m["id"] for m in discover_modules()}
    ov = {r["id"]: r for r in roles.roles_overview()["roles"]}
    for rid in ("default", "media", "network"):
        m = roles.load_roles()[rid]
        ids = m["required_modules"]
        if ids == "*":
            continue
        for mid in ids:
            assert mid in known, "роль %s ссылается на неизвестный %s" % (rid, mid)
    for r in ov.values():
        known_wanted = [i for i in r["required"] + r["optional"] if i != "*"]
        assert set(r["missing"]) <= set(known_wanted)
        for mid in r["missing"]:
            assert mid not in known


def test_always_on_in_every_profile():
    for rid in roles.load_roles():
        targets = roles._targets(rid)
        for mid in roles.ALWAYS_ON:
            if mid in targets:
                assert targets[mid] is True, "%s выключает %s" % (rid, mid)


def test_active_role_fallback(monkeypatch):
    monkeypatch.setattr(roles, "_read_state", lambda: {"active": "nope"})
    assert roles.active_role() == "default"
    monkeypatch.setattr(roles, "_read_state", lambda: {"active": "media"})
    assert roles.active_role() == "media"
    assert roles.set_active("nope") is False


def test_role_module_ids():
    assert set(roles.role_module_ids("default")) == {
        m["id"] for m in discover_modules()}
    media = roles.role_module_ids("media")
    assert "torrent" in media and "notes" not in media
    assert roles.role_module_ids("nope") is None


def _patch_apply_env(monkeypatch, status_fn):
    """Изолировать apply_role от /etc и системы."""
    monkeypatch.setattr(roles, "module_status", lambda mid: (True, True))
    monkeypatch.setattr(roles, "set_module_status",
                        lambda mid, installed=None, enabled=None: None)
    monkeypatch.setattr(roles, "set_active", lambda rid: True)
    monkeypatch.setattr(roles, "compute_status", status_fn)
    monkeypatch.setattr(roles, "_missing_apt_packages", lambda p: [])
    monkeypatch.setattr(roles, "status_context",
                        lambda: {"arch": "aarch64", "caps": {}})


def test_apply_role(monkeypatch):
    known = [m["id"] for m in discover_modules()]
    st = {mid: [True, True] for mid in known}
    st["torrent"] = [True, False]    # модуль роли выключен
    st["camera"] = [False, False]    # не установлен
    calls, acts = [], []

    monkeypatch.setattr(roles, "module_status",
                        lambda mid: (st[mid][0], st[mid][1]))

    def fake_set(mid, installed=None, enabled=None):
        calls.append((mid, enabled))
        if installed is not None:
            st[mid][0] = installed
        if enabled is not None:
            st[mid][1] = enabled

    monkeypatch.setattr(roles, "set_module_status", fake_set)
    monkeypatch.setattr(roles, "set_active", lambda rid: acts.append(rid) or True)
    monkeypatch.setattr(roles, "compute_status",
                        lambda m, e, c, mp=None: "active")
    monkeypatch.setattr(roles, "_missing_apt_packages", lambda p: [])
    monkeypatch.setattr(roles, "status_context",
                        lambda: {"arch": "aarch64", "caps": {}})

    res = roles.apply_role("media")
    assert res["ok"] and res["active"] == "media"
    assert acts == ["media"]
    assert "torrent" in res["enabled"]            # был выкл → включён
    assert "notes" in res["disabled"]             # вне роли → выключен
    assert "sys-settings" not in res["disabled"]  # ALWAYS_ON защищён
    assert "camera" not in [c[0] for c in calls]  # не установлен → не тронут
    assert res["skipped"] == []
    assert st["torrent"][1] is True


def test_apply_role_skips_hardware(monkeypatch):
    def status_fn(m, e, c, mp=None):
        if m.get("id") == "wifianalyzer":
            return "requires-hardware"
        return "active"

    _patch_apply_env(monkeypatch, status_fn)
    res = roles.apply_role("network")
    skipped = [s["id"] for s in res["skipped"]]
    assert "wifianalyzer" in skipped
    assert res["skipped"][0]["status"] == "requires-hardware"
    assert "wifianalyzer" not in res["enabled"]


def test_apply_unknown_role():
    assert roles.apply_role("nope")["ok"] is False


def test_roles_overview_shape():
    ov = roles.roles_overview()
    rids = set(roles.load_roles())
    assert ov["active"] in rids
    assert {r["id"] for r in ov["roles"]} == rids
    for r in ov["roles"]:
        mids = {m["id"] for m in r["modules"]}
        for mid in roles.ALWAYS_ON:
            assert mid in mids, "%s без %s" % (r["id"], mid)
        for m in r["modules"]:
            assert m["status"] in MODULE_STATUSES
            assert isinstance(m["always_on"], bool)
            assert isinstance(m["enabled"], bool)
            assert m["role"] in ("required", "optional", "conflict", "always")
        # контракт Roles 2.0 (аддитивно к 1.1)
        for key in ("required", "optional", "missing", "not_installed",
                    "conflicts", "blockers", "ready",
                    "security_profile", "recommended_configuration"):
            assert key in r, "%s без %s" % (r["id"], key)
        assert isinstance(r["ready"], bool)
        assert isinstance(r["blockers"], list)
        assert isinstance(r["recommended_configuration"], dict)


def test_api_roles(client):
    r = client.get("/api/roles")
    assert r.status_code == 200
    d = r.get_json()
    assert "active" in d
    assert len(d["roles"]) == len(roles.load_roles())


def test_roles_page(client):
    r = client.get("/roles")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Роли" in html
    assert "/roles/media/apply" in html
    assert ("Применить" in html) or ("Переприменить" in html)


def test_api_apply_unknown_role_json(client):
    r = client.post("/api/roles/nope/apply")
    assert r.status_code == 200
    assert r.get_json()["ok"] is False


def test_nav_roles_admin_only():
    admin_urls = {e["url"] for g in nav_groups(True) for e in g["entries"]}
    user_urls = {e["url"] for g in nav_groups(False) for e in g["entries"]}
    assert "/roles" in admin_urls
    assert "/roles" not in user_urls


# --- Roles 2.0: валидация, загрузчик, blockers, conflicts -------------------

def test_validate_role_ok_and_errors():
    good = {"id": "x", "name": "X"}
    assert roles.validate_role(good, "x") == []
    assert any("не объект" in e for e in roles.validate_role(None))
    assert any("не совпадает" in e for e in roles.validate_role(good, "y"))
    assert any("name" in e for e in roles.validate_role({"id": "x"}))
    e = roles.validate_role(
        {"id": "x", "name": "X", "conflicts": ["sys-settings"]}, "x")
    assert any("ALWAYS_ON" in x for x in e)
    e = roles.validate_role(
        {"id": "x", "name": "X", "optional_modules": ["*"]}, "x")
    assert any("*" in x for x in e)
    e = roles.validate_role(
        {"id": "x", "name": "X", "capabilities": ["BAD"]}, "x")
    assert any("capabilities" in x for x in e)
    e = roles.validate_role(
        {"id": "x", "name": "X", "required_modules": "nope",
         "dependencies": [], "recommended_configuration": [],
         "security_profile": 5}, "x")
    assert len(e) >= 4


def test_load_roles_skips_invalid(tmp_path, monkeypatch):
    (tmp_path / "ok.json").write_text(
        json.dumps({"id": "ok", "name": "Ok"}), encoding="utf-8")
    (tmp_path / "bad.json").write_text(
        json.dumps({"id": "bad", "conflicts": ["sys-db"]}), encoding="utf-8")
    monkeypatch.setattr(roles, "ROLES_DIR", str(tmp_path))
    _reset_cache(monkeypatch)
    got = roles.load_roles(force=True)
    assert "ok" in got and "bad" not in got


def _role_dir(tmp_path, monkeypatch, role):
    (tmp_path / ("%s.json" % role["id"])).write_text(
        json.dumps(role), encoding="utf-8")
    monkeypatch.setattr(roles, "ROLES_DIR", str(tmp_path))
    _reset_cache(monkeypatch)
    roles.load_roles(force=True)


def test_role_blockers(tmp_path, monkeypatch):
    _role_dir(tmp_path, monkeypatch, {
        "id": "b", "name": "B",
        "required_modules": ["monitoring"],
        "capabilities": ["camera.usb0", "network"],
        "hardware_requirements": {"arch": ["armv71"],
                                  "tools": ["gpu-tool"]},
        "dependencies": {"apt": ["nope-pkg"],
                         "services": ["weird.service"]},
    })
    ctx = {"arch": "aarch64",
           "caps": {"camera": {"usb0": {"state": "absent"}}, "tools": {}}}
    monkeypatch.setattr(roles, "_missing_apt_packages", lambda p: ["nope-pkg"])
    import core.services as svc
    monkeypatch.setattr(
        svc, "status",
        lambda unit, timeout=5: {"unit": unit, "active": "unknown",
                                 "enabled": "unknown"})
    blockers = roles.role_blockers("b", ctx)
    text = " ".join(blockers)
    assert "архитектура" in text
    assert "camera.usb0" in text
    assert "gpu-tool" in text
    assert "nope-pkg" in text
    assert "нет возможности network" not in text  # голая группа — не гадаем
    assert "weird.service" not in text           # active unknown — не гадаем
    # совместимая система → blockers пуст
    ok_ctx = {"arch": "armv71",
              "caps": {"camera": {"usb0": {"state": "present"}},
                       "tools": {"gpu-tool": {"state": "present"}}}}
    monkeypatch.setattr(roles, "_missing_apt_packages", lambda p: [])
    monkeypatch.setattr(
        svc, "status",
        lambda unit, timeout=5: {"unit": unit, "active": "active",
                                 "enabled": "enabled"})
    assert roles.role_blockers("b", ok_ctx) == []


def test_apply_role_refused_when_blockers(tmp_path, monkeypatch):
    _role_dir(tmp_path, monkeypatch, {
        "id": "hw", "name": "HW",
        "required_modules": ["monitoring"],
        "hardware_requirements": {"arch": ["armv71"]},
    })
    monkeypatch.setattr(roles, "status_context",
                        lambda: {"arch": "aarch64", "caps": {}})
    res = roles.apply_role("hw")
    assert res["ok"] is False
    assert "не подходит" in res["error"]


def test_targets_conflicts(tmp_path, monkeypatch):
    _role_dir(tmp_path, monkeypatch, {
        "id": "c", "name": "C",
        "required_modules": ["monitoring"],
        "conflicts": ["notes"],
    })
    t = roles._targets("c")
    assert t["notes"] is False          # конфликт роли выключается
    assert t["monitoring"] is True
    for mid in roles.ALWAYS_ON:
        if mid in t:
            assert t[mid] is True


def test_overview_conflict_and_role_flags(tmp_path, monkeypatch):
    _role_dir(tmp_path, monkeypatch, {
        "id": "c", "name": "C",
        "required_modules": ["monitoring"],
        "optional_modules": ["disks"],
        "conflicts": ["notes"],
    })
    ov = roles.roles_overview()
    r = next(x for x in ov["roles"] if x["id"] == "c")
    byid = {m["id"]: m for m in r["modules"]}
    assert byid["monitoring"]["role"] == "required"
    assert byid["disks"]["role"] == "optional"
    assert byid["notes"]["role"] == "conflict"
    assert r["conflicts"] == ["notes"]


def test_mutating_routes_have_edit_guard():
    """B-04: инвариант аудита — mutation-маршрут не может быть login-only.

    Каждый @app.route с POST/PUT/DELETE/PATCH обязан иметь @can_edit
    или @admin_required (иначе guest-role пишет в систему, §11).
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    files = sorted((root / "modules").glob("*.py")) + [root / "app.py"]
    offenders = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if not lines[i].lstrip().startswith("@app.route"):
                i += 1
                continue
            j = i
            while j < len(lines) and not lines[j].lstrip().startswith("def "):
                j += 1
            block = lines[i:j] if j <= len(lines) else lines[i:]
            text = "\n".join(block)
            m = re.search(r"methods\s*=\s*\[([^\]]*)\]", text)
            mut = bool(m) and any(
                meth in m.group(1)
                for meth in ("POST", "PUT", "DELETE", "PATCH"))
            decs = [x.strip() for x in block if x.strip().startswith("@")]
            if (mut and "@login_required" in decs
                    and "@can_edit" not in decs
                    and "@admin_required" not in decs):
                rm = re.search(r'"(/[^"]*)"', text)
                offenders.append("%s:%d %s" % (
                    path.name, i + 1, rm.group(1) if rm else "?"))
            i = j + 1
    assert offenders == [], "нет @can_edit/@admin_required: %s" % offenders




# --- Task3: атомарная запись roles.json (real file, не патченый _read_state) ---

def test_write_state_atomic_and_corrupt_fallback(tmp_path, monkeypatch):
    """Сбой записи не портит roles.json; битый файл => роль default."""
    p = str(tmp_path / "roles.json")
    monkeypatch.setattr(roles, "STATE_PATH", p)

    # успех: валидный JSON, tmp-обломков нет, состояние читается обратно
    assert roles._write_state({"active": "media"}) is True
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"active": "media"}
    assert not os.path.exists(p + ".tmp")
    assert roles._read_state() == {"active": "media"}

    # неатомарный провал невозможен: цель цела, tmp убран, False наверх
    class _NotJson:
        pass

    assert roles._write_state({"active": _NotJson()}) is False
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"active": "media"}
    assert not os.path.exists(p + ".tmp")

    # битый (рваный) файл на входе => state пуст => active_role == default
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"active": "med')
    assert roles._read_state() == {}
    assert roles.active_role() == "default"

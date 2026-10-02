# -*- coding: utf-8 -*-
"""Unit: automation engine (PHASE 2.0-13, §17): Event → Rule → Action."""
import sqlite3
import time

import pytest

import app
import core.automation as au
import core.events as ev
import modules.auth as auth


@pytest.fixture(autouse=True)
def _csrf_off():
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = False
    yield
    app.app.config["WTF_CSRF_ENABLED"] = old


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


@pytest.fixture()
def auto_db(devices_db, monkeypatch):
    """Полная схема + чистое состояние engine (без подписок/cooldown)."""
    monkeypatch.setattr(au, "_last_fired", {})
    monkeypatch.setattr(au, "_subscription", None)
    yield devices_db
    au.stop()
    au._last_fired.clear()


@pytest.fixture()
def con(auto_db):
    c = sqlite3.connect(auto_db)
    yield c
    c.close()


def _payload(name="device.offline", ip="10.0.0.1", **kw):
    return {"name": name, "ip": ip, "hostname": None, "mac": None,
            "severity": "warning", "source": "discovery",
            "metadata": kw.pop("metadata", None), "timestamp": "t"}


# --- CRUD -------------------------------------------------------------------

def test_crud_roundtrip(con):
    assert au.list_rules(con) == []
    rid, err = au.add_rule(con, {
        "name": "offline-alert", "event": "device.offline",
        "actions": [{"type": "log", "message": "host down"}],
        "cooldown_sec": 60,
    })
    assert err is None and rid
    rules = au.list_rules(con)
    assert len(rules) == 1
    r = rules[0]
    assert r["name"] == "offline-alert" and r["enabled"] is True
    assert r["event"] == "device.offline"
    assert r["cooldown_sec"] == 60
    assert r["actions"] == [{"type": "log", "message": "host down"}]
    assert r["fired_count"] == 0

    assert au.set_enabled(con, rid, False) is True
    assert au.get_rule(con, rid)["enabled"] is False
    assert au.set_enabled(con, 999, True) is False

    assert au.delete_rule(con, rid) is True
    assert au.list_rules(con) == []
    assert au.delete_rule(con, rid) is False


def test_validate_rejects(con):
    valid = {"name": "x", "event": "system.warning",
             "actions": [{"type": "log"}]}
    assert au.validate_rule(dict(valid, name=""))[0] is None
    assert au.validate_rule(dict(valid, event="nope"))[0] is None
    assert au.validate_rule(dict(valid, actions=[]))[0] is None
    assert au.validate_rule(
        dict(valid, actions=[{"type": "unknown"}]))[0] is None
    assert au.validate_rule(
        dict(valid, actions=[{"type": "event", "event": "bad"}]))[0] is None
    assert au.validate_rule(dict(valid, cooldown_sec=-1))[0] is None
    assert au.validate_rule(dict(valid, cooldown_sec="x"))[0] is None
    ok, err = au.validate_rule(valid)
    assert ok is not None and err is None
    # событие с alias (legacy) тоже валидно
    assert au.validate_rule(dict(valid, event="OFFLINE"))[0] is not None


# --- engine -----------------------------------------------------------------

def test_handle_event_triggers_rule(con):
    rid, _ = au.add_rule(con, {
        "name": "r1", "event": "device.offline",
        "actions": [{"type": "log", "message": "down"}],
    })
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid)["fired_count"] == 1
    # другое событие — не матчится
    au.handle_event(_payload("device.online"))
    assert au.get_rule(con, rid)["fired_count"] == 1


def test_handle_event_dual_read_match(con):
    """Правило с legacy-именем ловит namespace-событие и обратно."""
    rid_off, _ = au.add_rule(con, {
        "name": "legacy", "event": "OFFLINE",
        "actions": [{"type": "log"}],
    })
    rid_ns, _ = au.add_rule(con, {
        "name": "ns", "event": "job.completed",
        "actions": [{"type": "log"}],
    })
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid_off)["fired_count"] == 1
    assert au.get_rule(con, rid_ns)["fired_count"] == 0
    au.handle_event(_payload("job.completed"))
    assert au.get_rule(con, rid_ns)["fired_count"] == 1


def test_cooldown_suppresses(con):
    rid, _ = au.add_rule(con, {
        "name": "cd", "event": "device.offline",
        "actions": [{"type": "log"}], "cooldown_sec": 3600,
    })
    au.handle_event(_payload("device.offline"))
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid)["fired_count"] == 1
    # cooldown истёк — снова срабатывает
    au._last_fired[rid] = time.time() - 7200
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid)["fired_count"] == 2


def test_disabled_rule_ignored(con):
    rid, _ = au.add_rule(con, {
        "name": "off", "event": "device.offline",
        "actions": [{"type": "log"}], "enabled": False,
    })
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid)["fired_count"] == 0


def test_event_action_and_loop_guard(con):
    """Action event создаёт системное событие; петля запрещена (§17)."""
    rid_a, _ = au.add_rule(con, {
        "name": "a", "event": "device.offline",
        "actions": [{"type": "event", "event": "system.warning",
                     "metadata": {"note": "from-a"}}],
    })
    rid_b, _ = au.add_rule(con, {
        "name": "b", "event": "system.warning",
        "actions": [{"type": "log"}],
    })
    au.handle_event(_payload("device.offline"))
    assert au.get_rule(con, rid_a)["fired_count"] == 1
    # порождённое automation событие НЕ триггерит правило b (нет петли)
    assert au.get_rule(con, rid_b)["fired_count"] == 0
    rows = [r[0] for r in con.execute(
        "SELECT event FROM events WHERE source='automation'")]
    assert rows == ["system.warning"]


def test_start_stop_subscribe(auto_db):
    con = sqlite3.connect(auto_db)
    try:
        rid, _ = au.add_rule(con, {
            "name": "s", "event": "system.warning",
            "actions": [{"type": "log"}],
        })
        con.close()
        assert au.start() is True
        assert au.start() is False  # идемпотентно
        ev.emit("system.warning", ip="10.0.0.9", source="tests")
        con = sqlite3.connect(auto_db)
        assert au.get_rule(con, rid)["fired_count"] == 1
        con.close()
        assert au.stop() is True
        assert au.stop() is False
        ev.emit("system.warning", ip="10.0.0.9", source="tests")
        con = sqlite3.connect(auto_db)
        assert au.get_rule(con, rid)["fired_count"] == 1
    finally:
        con.close()


# --- API --------------------------------------------------------------------

def test_api_rules_crud(client, auto_db):
    r = client.get("/api/automation/rules")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "rules": [], "actions": ["event",
                                                                 "log"]}

    r = client.post("/api/automation/rules", json={
        "name": "api-rule", "event": "camera.motion",
        "actions": [{"type": "log", "message": "motion"}],
    })
    assert r.status_code == 201
    rid = r.get_json()["id"]

    r = client.get("/api/automation/rules")
    rules = r.get_json()["rules"]
    assert len(rules) == 1 and rules[0]["id"] == rid

    r = client.post(f"/api/automation/rules/{rid}/toggle")
    assert r.status_code == 200 and r.get_json()["enabled"] is False
    r = client.post(f"/api/automation/rules/{rid}/toggle")
    assert r.get_json()["enabled"] is True

    r = client.post("/api/automation/rules", json={
        "name": "bad", "event": "no-such-event",
        "actions": [{"type": "log"}],
    })
    assert r.status_code == 400 and r.get_json()["ok"] is False

    assert client.delete(f"/api/automation/rules/{rid}").status_code == 200
    assert client.delete(f"/api/automation/rules/{rid}").status_code == 404
    assert client.post(
        "/api/automation/rules/123/toggle").status_code == 404


def test_api_rules_auth_required(client, auto_db):
    with client.session_transaction() as s:
        s.clear()
    r = client.get("/api/automation/rules")
    assert r.status_code in (302, 401)


def test_page_renders(client, auto_db):
    r = client.get("/automation")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Automation" in html
    assert "/api/automation/rules" in html

# -*- coding: utf-8 -*-
"""Unit: SDR-радио — страница виджета, статус, валидация частоты."""
import time

import pytest

import app
import modules.auth as auth
import modules.sdr_routes as sdr


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
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
    app.app.config["WTF_CSRF_ENABLED"] = True


def test_sdr_page_renders(client):
    r = client.get("/apps/sdr")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "sdr-waterfall" in html
    assert "socket.io.js" in html
    assert "/api/sdr/freq" in html


def test_sdr_status_shape(client, monkeypatch):
    monkeypatch.setattr(sdr, "_rtl_reachable", lambda timeout=1.5: False)
    r = client.get("/api/sdr/status")
    assert r.status_code == 200
    d = r.get_json()
    for key in ("dongle", "streaming", "freq", "rate", "last_iq", "error"):
        assert key in d
    assert d["dongle"] is False
    assert d["streaming"] is False


def test_sdr_freq_validation(client):
    r = client.post("/api/sdr/freq", json={"freq": "abc"})
    assert r.status_code == 400
    r = client.post("/api/sdr/freq", json={"freq": 1000})
    assert r.status_code == 400
    r = client.post("/api/sdr/freq", json={"freq": 2_000_000_000})
    assert r.status_code == 400


def test_sdr_freq_ok_without_stream(client):
    r = client.post("/api/sdr/freq", json={"freq": 101_500_000})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["freq"] == 101_500_000
    assert d["applied"] is False


def test_sdr_rtl_start_failure(client, monkeypatch):
    def boom(*a, **k):
        raise sdr.subprocess.SubprocessError("no systemd in tests")

    monkeypatch.setattr(sdr.subprocess, "run", boom)
    r = client.post("/api/sdr/rtl/start")
    assert r.status_code == 500
    assert r.get_json()["ok"] is False

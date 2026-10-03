# -*- coding: utf-8 -*-
"""Unit: настройки скана через UI (№63, P6) — deep-merge, ifaces API, форма."""
import time

import pytest

import app
import modules.auth as auth
import modules.core_routes as core_routes


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


@pytest.fixture()
def guest_client(monkeypatch):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {
            "admin": {"enabled": True, "role": "admin"},
            "guest": {"enabled": True, "role": "guest"},
        },
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "guest"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = True


def test_settings_post_deep_merge_keeps_other_keys(client, monkeypatch):
    """Частичный POST не стирает соседние ключи network (баг старого update)."""
    saved = {}

    def _load():
        return {
            "network": {"self_ips": ["192.168.3.243"], "wifi_ifaces": ["wlan0"],
                        "subnet": "192.168.3.0/24"},
            "weather": {"timezone": "Europe/Moscow"},
        }

    def _save(data):
        saved.update(data)
        return True

    monkeypatch.setattr(core_routes, "load_settings", _load)
    monkeypatch.setattr(core_routes, "save_settings", _save)

    r = client.post("/api/settings", json={
        "network": {"scan_ifaces": ["eth0"], "scan_interval": 45},
    })
    assert r.status_code == 200
    net = saved["network"]
    assert net["scan_ifaces"] == ["eth0"]
    assert net["scan_interval"] == 45
    assert net["self_ips"] == ["192.168.3.243"]  # сохранён
    assert net["wifi_ifaces"] == ["wlan0"]        # сохранён
    assert net["subnet"] == "192.168.3.0/24"      # сохранён
    assert saved["weather"]["timezone"] == "Europe/Moscow"  # другой раздел цел


def test_network_ifaces_admin(client):
    r = client.get("/api/network/ifaces")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data.get("ifaces"), list)
    # loopback в список скана не попадает
    assert all(i["name"] != "lo" for i in data["ifaces"])
    for i in data["ifaces"]:
        assert set(i) >= {"name", "state", "ip"}


def test_network_ifaces_guest_403(guest_client):
    assert guest_client.get("/api/network/ifaces").status_code == 403


def test_system_page_has_scan_ifaces_form(client):
    html = client.get("/system").get_data(as_text=True)
    assert "cfg-scan-iface" in html          # чекбоксы интерфейсов
    assert "cfg-scan-enabled" in html        # тумблер фонового скана
    assert "/api/network/ifaces" in html     # загрузка списка


def test_session_cookie_name_from_settings(monkeypatch):
    """settings.web.session_cookie → имя куки сессии.

    Куки не изолируются по порту: стенд 2.0 рядом с 1.1 на одном хосте
    делил бы куку `session` и затирал сессии боевой панели («The CSRF
    session token is missing»). Без ключа — прежний дефолт «session».
    """
    monkeypatch.setattr(
        app, "load_settings",
        lambda: {"web": {"session_cookie": "session_ld20"}})
    assert app._session_cookie_name() == "session_ld20"
    monkeypatch.setattr(app, "load_settings", lambda: {"web": {}})
    assert app._session_cookie_name() == "session"
    monkeypatch.setattr(app, "load_settings", lambda: {})
    assert app._session_cookie_name() == "session"
    # конфиг приложения — непустая строка (имя куки установлено)
    assert isinstance(app.app.config.get("SESSION_COOKIE_NAME"), str)
    assert app.app.config.get("SESSION_COOKIE_NAME")

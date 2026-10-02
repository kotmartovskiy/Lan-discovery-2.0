# -*- coding: utf-8 -*-
"""Live-смоук: health/аутентификация/API против работающей панели.

Запуск: `pytest -m live` (по умолчанию `-m "not live"`).
"""
import json

import pytest

pytestmark = pytest.mark.live


def test_health_shape(panel_url):
    import requests
    resp = requests.get(panel_url + "/api/health", timeout=10)
    assert resp.status_code == 200
    d = json.loads(resp.text)
    assert d["db"]["status"] == "ok"
    assert d["db"]["user_version"] in (2, 3)  # 2 = ветка 1.1, 3 = 2.0
    assert d["version"]
    assert set(d["platform"]) == {"board", "arch", "system", "emmc", "sd",
                                  "hdd", "thermal_zone"}
    assert isinstance(d["capabilities"], dict) and d["capabilities"]
    assert d.get("last_discovery") is not None


def test_anonymous_api_events_redirect(panel_url):
    import requests
    r = requests.get(panel_url + "/api/events?limit=3", timeout=10,
                     allow_redirects=False)
    assert r.status_code == 302


def test_login_and_events(admin_session, panel_url):
    r = admin_session.get(panel_url + "/api/events?limit=5", timeout=10)
    assert r.status_code == 200
    d = json.loads(r.text)
    assert d["ok"] is True
    assert isinstance(d["events"], list)


def test_pages_smoke(admin_session, panel_url):
    for path in ("/", "/history", "/currencies", "/apps"):
        r = admin_session.get(panel_url + path, timeout=15)
        assert r.status_code == 200, (path, r.status_code)


def test_api_currencies(admin_session, panel_url):
    r = admin_session.get(panel_url + "/api/currencies", timeout=15)
    assert r.status_code == 200

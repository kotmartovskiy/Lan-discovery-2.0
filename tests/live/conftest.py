# -*- coding: utf-8 -*-
"""Live-фикстуры: панель должна быть достижима, иначе skip."""
import os

import pytest
import requests

PANEL_URL = os.environ.get("LAN_PANEL_URL", "http://192.168.3.243:8080")


@pytest.fixture(scope="session")
def panel_url():
    try:
        r = requests.get(PANEL_URL + "/api/health", timeout=5)
        if r.status_code == 200:
            return PANEL_URL
    except Exception:
        pass
    pytest.skip("панель недоступна: %s (LAN_PANEL_URL)" % PANEL_URL)


@pytest.fixture(scope="session")
def admin_session(panel_url):
    """Авторизованная сессия admin (admin/1234 — тестовый пароль)."""
    s = requests.Session()
    r = s.get(panel_url + "/login", timeout=10)
    m = None
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    data = {"username": "admin",
            "password": os.environ.get("LAN_PANEL_PASS", "1234")}
    if m:
        data["csrf_token"] = m.group(1)
    r = s.post(panel_url + "/login", data=data, timeout=10,
               allow_redirects=False)
    assert r.status_code in (200, 302), r.status_code
    return s

# -*- coding: utf-8 -*-
"""Live: security-регресс P1-10/P0 против работающей панели (PHASE 8).

Перенос ad-hoc `/tmp/test_p10_env.py` (27 чеков) в pytest: заголовки,
cookie-флаги, чистый /help, no-store, CSRF/авторизация.
Запуск: `pytest -m live tests/live/test_security.py`.
"""
import json
import re

import pytest
import requests

pytestmark = pytest.mark.live


def _csrf(session, panel_url):
    r = session.get(panel_url + "/login", timeout=10)
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    return r, (m.group(1) if m else "")


def test_headers_on_login_anonymous(panel_url):
    r, _ = _csrf(requests.Session(), panel_url)
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("Referrer-Policy") == "same-origin"
    # PHASE 16 №60: CSP без внешних CDN в script-src
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp, "CSP missing"
    script_src = next(p.strip() for p in csp.split(";")
                      if p.strip().startswith("script-src"))
    assert "http" not in script_src


def test_login_sets_safe_session_cookie(panel_url):
    s = requests.Session()
    r, token = _csrf(s, panel_url)
    r2 = s.post(panel_url + "/login",
                data={"username": "admin", "password": "1234",
                      "csrf_token": token},
                allow_redirects=False, timeout=10)
    assert r2.status_code == 302
    sc = r2.headers.get("Set-Cookie", "")
    assert "HttpOnly" in sc, sc[:160]
    assert "SameSite=Lax" in sc, sc[:160]
    assert "Secure" not in sc, sc[:160]


def test_headers_and_no_store_on_api(admin_session, panel_url):
    r = admin_session.get(panel_url + "/api/health", timeout=10)
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "no-store" in r.headers.get("Cache-Control", "")


@pytest.mark.parametrize("path", ["/", "/system", "/static/style.css"])
def test_headers_on_pages(admin_session, panel_url, path):
    r = admin_session.get(panel_url + path, timeout=15)
    assert r.status_code == 200, (path, r.status_code)
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_help_page_has_no_password_literals(admin_session, panel_url):
    r = admin_session.get(panel_url + "/help", timeout=15)
    assert r.status_code == 200
    assert "root / 1234" not in r.text
    assert "(пароль: 1234)" not in r.text
    assert "пароль: 1234" not in r.text
    # UX-контент сохранён (IP-таблица хостов)
    assert "192.168.3.243" in r.text
    assert "X96 Max" in r.text


def test_login_post_without_csrf_rejected(panel_url):
    """POST /login без CSRF: вход не совершается.

    1.1: голый 400. 2.0 (ловушка стенда N6): свежая форма с подсказкой
    «Форма устарела» (200) вместо бэкенд-текста — но 302/входа нет,
    валидация токена не ослаблена.
    """
    h = requests.get(panel_url + "/api/health", timeout=10).json()
    v = str(h.get("version") or "")
    r = requests.post(panel_url + "/login",
                      data={"username": "admin", "password": "x"},
                      allow_redirects=False, timeout=10)
    if v.startswith("2."):
        assert r.status_code == 200          # не 302 → не залогинился
        assert "Форма устарела" in r.text    # человеку понятный ответ
        assert "csrf_token" in r.text        # свежая форма для повтора
    else:
        assert r.status_code == 400


def test_api_status_requires_session(panel_url):
    r = requests.get(panel_url + "/api/status", allow_redirects=False,
                     timeout=10)
    assert r.status_code == 302


def test_health_shape_security_fields(admin_session, panel_url):
    """Регресс P1-9 в составе security-набора: поля health на месте."""
    r = admin_session.get(panel_url + "/api/health", timeout=10)
    d = json.loads(r.text)
    assert d.get("version")
    assert isinstance(d.get("uptime"), dict)
    assert isinstance(d.get("last_discovery"), dict)
    db = d.get("db")
    assert isinstance(db, dict) and db.get("status") == "ok"

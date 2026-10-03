# -*- coding: utf-8 -*-
"""Unit: security-регресс (PHASE 8) — заголовки, cookie, аноним-поведение.

Фиксирует hardening P1-10/P0: nosniff/XFO/Referrer-Policy на всех
ответах, HttpOnly+SameSite=Lax у session-cookie, аноним → 302 на
/login, mutating-POST без CSRF → 400. Ловится в CI (без живой панели).
PHASE 16 №60: CSP (без внешних CDN в script-src) и локальный vendor
xterm/socket.io для терминала.
"""
from pathlib import Path

import pytest

import app


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = False
    yield app.app.test_client()
    app.app.config["WTF_CSRF_ENABLED"] = old


@pytest.fixture()
def csrf_client():
    """CSRF включён — для проверки отказа анониму."""
    app.app.config["TESTING"] = True
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = True
    yield app.app.test_client()
    app.app.config["WTF_CSRF_ENABLED"] = old


def test_security_headers_on_login(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("Referrer-Policy") == "same-origin"


def test_stale_csrf_on_login_rerenders_form(csrf_client):
    """Мёртвый CSRF на /login → свежая форма (200), а не голый 400.

    Ловушка стенда N6: восстановленная вкладка/кэш формы без живой куки
    («The CSRF session token is missing») — юзер должен просто нажать
    «Войти», без ручного F5. Валидация не ослаблена: чужой токен
    отклонён, рендерится только форма.
    """
    r = csrf_client.post("/login", data={
        "username": "admin", "password": "1234",
        "csrf_token": "deadbeef",
    })
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "csrf_token" in html            # свежая форма с новым токеном
    assert "Форма устарела" in html


def test_security_headers_on_api(client):
    r = client.get("/api/health")
    assert r.status_code in (200, 503)  # 503 — нет системных бинарей
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_security_headers_on_static(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_csp_header_present(client):
    """PHASE 16 №60: CSP есть, script-src без внешних CDN."""
    r = client.get("/login")
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp
    for directive in ("default-src 'self'", "script-src 'self'",
                      "style-src 'self'", "object-src 'none'",
                      "base-uri 'self'", "form-action 'self'",
                      "frame-ancestors 'self'"):
        assert directive in csp, directive
    script_src = next(part.strip() for part in csp.split(";")
                      if part.strip().startswith("script-src"))
    # в script-src только свои файлы и inline — внешних CDN нет
    assert "http" not in script_src
    assert "'unsafe-inline'" in script_src


def test_server_header_hides_versions():
    """§8.3 residual (находка №62): Server dev-сервера = «lan-discovery».

    Werkzeug шлёт Server в send_response (после заголовков приложения),
    поэтому version_string подменён в app.py — test client этого не видит,
    живая проверка curl есть в чек-листе пентеста.
    """
    from werkzeug.serving import WSGIRequestHandler
    assert WSGIRequestHandler.version_string(None) == "lan-discovery"


def test_terminal_vendor_local_only():
    """№60: терминал грузит vendor-копии локально (без CDN)."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "templates" / "apps" / "terminal.html").read_text(
        encoding="utf-8")
    assert "https://cdn" not in text
    assert "cdn.socket.io" not in text
    for vend in ("/static/vendor/xterm/xterm.min.css",
                 "/static/vendor/xterm/xterm.min.js",
                 "/static/vendor/xterm/addon-fit.min.js",
                 "/static/vendor/socket.io.min.js"):
        assert vend in text, vend
        f = root / vend.lstrip("/")
        assert f.is_file() and f.stat().st_size > 1000, vend


def test_session_cookie_flags(client):
    r = client.get("/login")
    sc = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in sc
    assert "SameSite=Lax" in sc
    assert "Secure" not in sc  # панель по HTTP в LAN — Secure сломает вход


def test_api_response_no_store(client):
    r = client.get("/api/health")
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_anonymous_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_anonymous_cannot_read_settings(client):
    r = client.get("/api/settings")
    assert r.status_code in (302, 401)
    if r.status_code == 302:
        assert "/login" in r.headers.get("Location", "")


def test_mutating_post_without_csrf_is_rejected(csrf_client):
    r = csrf_client.post("/api/settings", json={"web": {}})
    assert r.status_code == 400


def test_guest_cannot_read_settings(client, monkeypatch):
    """PHASE 16 №62 (находка): guest → 403 на GET /api/settings."""
    import time as _time

    from modules import auth
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"guest": {"password_hash": "x", "role": "guest",
                           "enabled": True},
                 "admin": {"password_hash": "x", "role": "admin",
                           "enabled": True}})
    with client.session_transaction() as s:
        s["user"] = "guest"
        s["login_ts"] = _time.time()
    r = client.get("/api/settings")
    assert r.status_code == 403
    with client.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = _time.time()
    r = client.get("/api/settings")
    assert r.status_code == 200

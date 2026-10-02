# -*- coding: utf-8 -*-
"""Unit: a11y (STEP 11) — семантика base, skip-link, focus, лейблы, scope."""
import os
import time

import pytest

import app
import modules.auth as auth

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def test_base_semantic_markup(client, devices_db):
    html = client.get("/").get_data(as_text=True)
    assert '<a class="skip-link" href="#main">' in html
    assert '<main id="main" tabindex="-1">' in html
    assert '<nav class="tabs" aria-label="Разделы панели">' in html
    assert '<header class="header">' in html
    assert 'aria-hidden="true"' in html  # декоративный SVG в h1
    # skip-link должен идти раньше main (первый фокус на странице)
    assert html.index('class="skip-link"') < html.index('<main id="main"')


def test_priority_tables_col_scope(client, devices_db):
    html = client.get("/history").get_data(as_text=True)
    assert html.count('<th scope="col">') == 6
    assert "<th>" not in html
    # 2.0-19: devices переехал на /devices (HOME = dashboard §22)
    devices = client.get("/devices").get_data(as_text=True)
    assert devices.count('<th scope="col">') >= 6
    assert "<th>" not in devices


def test_style_a11y_rules():
    with open(os.path.join(REPO, "static", "style.css"), encoding="utf-8") as f:
        css = f.read()
    assert ".skip-link:focus" in css
    assert ":focus-visible" in css
    assert "outline: 2px solid" in css


def test_login_template_a11y():
    with open(os.path.join(REPO, "templates", "login.html"),
              encoding="utf-8") as f:
        t = f.read()
    assert 'for="login-username"' in t
    assert 'for="login-password"' in t
    assert 'role="alert"' in t
    assert "outline:none" not in t  # видимый фокус на полях входа

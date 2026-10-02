# -*- coding: utf-8 -*-
"""Unit: responsive (STEP 10) — viewport в base + приоритетные страницы."""
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


def test_base_has_viewport(client, devices_db):
    html = client.get("/").get_data(as_text=True)
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html


def test_login_template_has_viewport():
    with open(os.path.join(REPO, "templates", "login.html"),
              encoding="utf-8") as f:
        assert "width=device-width" in f.read()


def test_priority_pages_table_wrap(client, devices_db):
    # 2.0-19: devices переехал на /devices (HOME = dashboard §22)
    for path in ("/devices", "/history"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="table-wrap"' in html, "нет table-wrap на %s" % path


def test_grid_pages_have_responsive_classes(client):
    mods = client.get("/modules").get_data(as_text=True)
    roles = client.get("/roles").get_data(as_text=True)
    assert "mod-grid" in mods
    assert "role-grid" in roles


def test_style_has_responsive_rules():
    with open(os.path.join(REPO, "static", "style.css"), encoding="utf-8") as f:
        css = f.read()
    assert ".table-wrap" in css
    assert "@media (max-width: 420px)" in css
    assert ".mod-grid" in css and ".role-grid" in css
    assert "max-width: 100%" in css
    assert "overflow-x: auto" in css


def test_base_links_style_with_cache_buster(client, devices_db):
    html = client.get("/").get_data(as_text=True)
    assert "/static/style.css?v=" in html

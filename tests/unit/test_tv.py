# -*- coding: utf-8 -*-
"""Unit: TV-режим (модуль tv) — /tv и манифест."""
import io
import json
import os
import time

import pytest

import app
import modules.auth as auth
from app import app as flask_app

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def test_tv_requires_login():
    c = flask_app.test_client()
    r = c.get("/tv")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_tv_page_renders(client):
    r = client.get("/tv")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "ld_tv_scale" in html
    assert "/api/dashboard" in html
    assert "/api/weather-status" in html
    assert "data-focus" in html


def test_tv_manifest_valid():
    from core.manifest import parse_version, validate_manifest
    path = os.path.join(_ROOT, "modules", "tv", "module.json")
    with io.open(path, encoding="utf-8") as f:
        m = json.load(f)
    assert m["id"] == "tv"
    assert parse_version(m.get("version")) is not None
    assert not validate_manifest(m)
    assert m.get("url") == "/tv"
    assert "/tv" in m.get("prefixes", [])


def test_tv_in_app_items():
    from core.module_loader import app_items
    items = app_items()
    tv = [it for it in items if it["module"] == "tv"]
    assert tv and tv[0]["url"] == "/tv"

# -*- coding: utf-8 -*-
"""Unit: Demo mode (спека §28, PHASE 2.0-21) — API adapter.

UI → core/demo (before_request в app.py) → Real API | Demo API:
GET /api/* → фикстуры, без фикстуры → безопасный 404, запись → 403,
HTML — production-шаблоны с плашкой (demo_mode в контексте).
"""
import json
import time

import pytest

import app as panel_app
import core.demo as demo
import modules.auth as auth


@pytest.fixture()
def demo_dir(tmp_path, monkeypatch):
    fx = tmp_path / "api"
    fx.mkdir()
    (fx / "dashboard.json").write_text(
        json.dumps({"ok": True, "source": "fixture",
                    "cards": ["всё хорошо"]}),
        encoding="utf-8")
    monkeypatch.setenv("LAN_DEMO", "1")
    monkeypatch.setenv("LAN_DEMO_DIR", str(tmp_path))
    demo.clear_cache()
    yield tmp_path
    monkeypatch.delenv("LAN_DEMO", raising=False)
    monkeypatch.delenv("LAN_DEMO_DIR", raising=False)
    demo.clear_cache()


@pytest.fixture()
def client(demo_dir, monkeypatch):
    panel_app.app.config["TESTING"] = True
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = panel_app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c


def test_adapter_serves_fixture_not_real(client):
    """GET /api/* → данные фикстуры, а не реальный роут."""
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.get_json()
    assert data["source"] == "fixture"
    assert "всё хорошо" in data["cards"]


def test_adapter_missing_fixture_is_safe_404(client):
    """Нет фикстуры → ok:false/demo:true (UI — empty state §23)."""
    r = client.get("/api/definitely-missing-endpoint")
    assert r.status_code == 404
    data = r.get_json()
    assert data["ok"] is False and data["demo"] is True


def test_adapter_forbids_write(client):
    """Demo read-only: POST/PUT/DELETE → 403 (до CSRF и роутов)."""
    r = client.post("/api/scan", data={})
    assert r.status_code == 403
    data = r.get_json()
    assert data["demo"] is True and data["ok"] is False

    r = client.delete("/api/jobs/1")
    assert r.status_code == 403


def test_adapter_requires_login(client, monkeypatch):
    """Фикстуры не отдаются без сессии (auth не ослабляется)."""
    anon = panel_app.app.test_client()
    r = anon.get("/api/dashboard")
    assert r.status_code == 401


def test_demo_banner_in_pages(client):
    """Production HTML + плашка демо-режима (§28: HTML не копируем)."""
    r = client.get("/help")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "ДЕМО-режим" in body and "demo-banner" in body


def test_real_api_when_demo_disabled(monkeypatch):
    """Demo off → адаптер не перехватывает запросы."""
    monkeypatch.delenv("LAN_DEMO", raising=False)
    monkeypatch.delenv("LAN_DEMO_DIR", raising=False)
    demo.clear_cache()
    assert demo.demo_enabled() is False

    panel_app.app.config["TESTING"] = True
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = panel_app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    r = c.get("/api/dashboard")
    assert r.status_code == 200
    data = r.get_json()
    # реальный ответ, а не fixture-маркер
    assert data.get("source") != "fixture"


def test_fixtures_loader(tmp_path):
    """Чтение <dir>/api/*.json → {путь: данные}, битые файлы пропускаются."""
    fx = tmp_path / "api"
    fx.mkdir()
    (fx / "jobs.json").write_text('{"ok": true, "jobs": []}',
                                  encoding="utf-8")
    (fx / "broken.json").write_text("не json", encoding="utf-8")
    demo.clear_cache()
    try:
        old = demo.fixtures_dir
        demo.fixtures_dir = lambda: str(tmp_path)
        try:
            data = demo.fixtures(force=True)
        finally:
            demo.fixtures_dir = old
    finally:
        demo.clear_cache()
    assert data["/api/jobs"] == {"ok": True, "jobs": []}
    assert "/api/broken" not in data

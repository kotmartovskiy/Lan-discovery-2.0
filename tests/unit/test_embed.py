# -*- coding: utf-8 -*-
"""Unit: встроенные веб-панели (tvheadend, netdata, dump1090) — модули
с плитками «Приложения», iframe-оверлеи, отдача статики dump1090
панелью, отключение модуля → 404."""
import os
import time

import pytest

import app
import modules.auth as auth
from core.module_loader import (
    app_items,
    desktop_categories,
    get_module,
    module_status,
)

EMBED_IDS = ("tvheadend", "netdata", "dump1090")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


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


def test_embed_modules_discovered():
    for mid in EMBED_IDS:
        m = get_module(mid)
        assert m, mid
        assert m.get("type") == "app", mid
        assert m.get("builtin") is True, mid
        app_meta = m.get("app") or {}
        assert app_meta.get("title"), mid
        assert app_meta.get("icon"), mid
        assert app_meta.get("category"), mid
        assert os.path.isfile(os.path.join(ROOT, "modules", mid, "help.md")), \
            "help: true требует help.md — %s" % mid
        assert module_status(mid) == (True, True), mid


def test_embed_tiles_in_app_items():
    keys = {it["key"] for it in app_items()}
    assert set(EMBED_IDS) <= keys
    flat = [t["key"] for c in desktop_categories() for t in c["tiles"]]
    assert set(EMBED_IDS) <= set(flat)


def test_dump1090_route_and_traversal(client):
    rules = {str(r) for r in app.app.url_map.iter_rules()}
    assert "/dump1090/" in rules
    # без файла статики — 404 (не 500); traversal — 403
    assert client.get("/dump1090/").status_code in (200, 404)
    assert client.get("/dump1090/../etc/passwd").status_code == 403


def test_dump1090_disabled_404(client, monkeypatch, tmp_path):
    from core import module_loader as ml
    monkeypatch.setattr(ml, "STATE_PATH", str(tmp_path / "modules.json"))
    assert ml.set_module_status("dump1090", enabled=False)
    try:
        assert client.get("/dump1090/gmap.html").status_code == 404
    finally:
        ml.set_module_status("dump1090", enabled=True)


def test_iframe_overlays_in_apps_template():
    tpl = os.path.join(ROOT, "templates", "apps.html")
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    for mid in EMBED_IDS:
        assert 'id="app-%s"' % mid in html, mid
        assert "closeApp('%s')" % mid in html, mid
    # внешние порты tvheadend/netdata и путь dump1090
    assert ":9981/" in html
    assert ":19999/" in html
    assert 'src="/dump1090/"' in html

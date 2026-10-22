# -*- coding: utf-8 -*-
"""Unit: cleanup (STEP 12) — legacy-удаления, возврат кнопок, help/API."""
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


def test_legacy_files_removed():
    assert not os.path.exists(os.path.join(REPO, "system_full.html"))
    assert not os.path.exists(
        os.path.join(REPO, "templates", "inventory_device.html"))


def test_dead_app_btn_css_removed():
    with open(os.path.join(REPO, "templates", "base_app.html"),
              encoding="utf-8") as f:
        t = f.read()
    assert ".app-btn" not in t
    assert ".app-header" in t  # сам шаблон жив


def test_emmc_block_has_backup_buttons():
    with open(os.path.join(REPO, "modules", "sys-emmc", "block.html"),
              encoding="utf-8") as f:
        t = f.read()
    assert 'id="btn-backup-create"' in t
    assert 'id="btn-backup-test"' in t
    assert 'id="btn-emmc-restore"' in t
    assert "startBackup()" in t and "testBackup()" in t
    assert "startEmmcRestore()" in t
    assert "current_user.role == 'admin'" in t  # опасные кнопки — admin


def test_inventory_device_redirects(client):
    r = client.get("/inventory/device/192.0.2.7")
    assert r.status_code == 302
    assert r.headers["Location"] == "/device/192.0.2.7"


def test_help_api_points_to_docs(client):
    html = client.get("/help").get_data(as_text=True)
    assert "API (JSON)" in html
    seg = html[html.index('id="api"'):html.index('id="admin-ops"')]
    assert "docs/API.md" in seg
    # ручная копия каталога (таблица) убрана из секции
    assert "help-table" not in seg


def test_inventory_page_regress(client):
    assert client.get("/inventory").status_code == 200

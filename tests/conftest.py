# -*- coding: utf-8 -*-
"""Общие фикстуры pytest (PHASE 13).

Unit-тесты не требуют сети и живой панели; live-тесты — маркер `live`
и фикстура `panel_url` (tests/live/conftest.py).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402


@pytest.fixture()
def events_con(tmp_path):
    """Чистое подключение с полной схемой events v2."""
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "events.db"))
    con.execute(
        "CREATE TABLE events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, ip TEXT, "
        "hostname TEXT, mac TEXT, event TEXT, severity TEXT, source TEXT, "
        "metadata TEXT)"
    )
    yield con
    con.close()


@pytest.fixture()
def devices_db(tmp_path, monkeypatch):
    """Чистая БД devices через полный init (миграции 0→2 + ensure)."""
    import core.db as dr
    db = str(tmp_path / "devices.db")
    monkeypatch.setattr(dr, "DB", db)
    monkeypatch.setattr(dr, "_init_done", False)
    dr.init_db_schema(force=True)
    yield db


@pytest.fixture()
def no_dns(monkeypatch):
    """Отключить DNS-резолв в reconcile (hostname=None)."""
    from core import discovery
    monkeypatch.setattr(discovery, "get_hostname", lambda ip: None)

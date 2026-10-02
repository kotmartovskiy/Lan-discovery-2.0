# -*- coding: utf-8 -*-
"""Unit: DB миграции и ensure-таблицы (PHASE 5: задачи 25-26)."""
import sqlite3

import core.db as dr

CORE_TABLES = {"devices", "events"}
ENSURE_TABLES = {"env_data", "mchs_alerts", "weather_alerts",
                 "weather_daily", "weather_forecast",
                 "weather_forecast_history", "weather_hourly",
                 "weather_observations"}


def _tables(db):
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def _columns(db, table):
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _indexes(db):
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def test_migration_v1_is_base_schema(tmp_path):
    db = str(tmp_path / "v1.db")
    con = sqlite3.connect(db)
    dr._migration_v1(con)
    ev_cols = _columns(db, "events")
    dev_cols = _columns(db, "devices")
    assert "severity" not in ev_cols and "metadata" not in ev_cols
    assert {"ip", "online", "mac", "first_seen", "last_seen",
            "appearances"} <= dev_cols
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()


def test_migration_v2_adds_columns(tmp_path):
    db = str(tmp_path / "v2.db")
    con = sqlite3.connect(db)
    dr._migration_v1(con)
    dr._migration_v2(con)
    con.commit()  # как в init_db_schema: бэкфилл+индекс в одной транзакции
    assert {"severity", "source", "metadata"} <= _columns(db, "events")
    assert {"hostname", "is_new", "misses", "name",
            "device_type"} <= _columns(db, "devices")
    assert "idx_events_event" in _indexes(db)
    con.close()


def test_migration_steps_idempotent(tmp_path):
    db = str(tmp_path / "idem.db")
    con = sqlite3.connect(db)
    dr._migration_v1(con)
    dr._migration_v2(con)
    con.commit()
    dr._migration_v1(con)  # повторный вызов не падает
    dr._migration_v2(con)
    con.commit()
    con.close()


def test_full_init_clean_db(tmp_path, monkeypatch):
    db = str(tmp_path / "clean.db")
    monkeypatch.setattr(dr, "DB", db)
    monkeypatch.setattr(dr, "_init_done", False)
    assert dr.init_db_schema(force=True) is True

    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    con.close()

    tables = _tables(db)
    assert CORE_TABLES <= tables
    assert ENSURE_TABLES <= tables
    assert {"idx_events_ip_id", "idx_events_event",
            "idx_weather_observations_timestamp_unique"} <= _indexes(db)


def test_init_twice_noop(tmp_path, monkeypatch):
    db = str(tmp_path / "twice.db")
    monkeypatch.setattr(dr, "DB", db)
    monkeypatch.setattr(dr, "_init_done", False)
    dr.init_db_schema(force=True)
    assert dr.init_db_schema() is False  # уже инициализирована


def test_retention_days_config(monkeypatch):
    from core import config
    monkeypatch.setattr(config, "load", lambda path=None: {})
    assert dr._retention_days() == 180
    monkeypatch.setattr(config, "load",
                        lambda path=None: {"events": {"retention_days": 0}})
    assert dr._retention_days() == 0
    monkeypatch.setattr(config, "load",
                        lambda path=None: {"events": {"retention_days": 30}})
    assert dr._retention_days() == 30

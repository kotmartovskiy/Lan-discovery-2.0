# -*- coding: utf-8 -*-
"""Unit: свежесть погодных данных в ридере /weather (прогноз/предупреждения)."""
import json
from datetime import datetime, timedelta

import pytest

import modules.weather_routes as wr

# Снапшот времени: тесты и ридер видят одно «сейчас» — нет гонки на
# границе суток (флэк CI: вставки считались от одного now(), фильтр
# прогноза — от другого).
_NOW = datetime.now()


@pytest.fixture()
def wdb(devices_db, monkeypatch):
    """Временная БД (все серверные таблицы) + переопределение путей ридера."""
    monkeypatch.setattr(wr, "DB", devices_db)
    monkeypatch.setattr(
        wr, "load_settings",
        lambda: {"weather": {"region_name": "Иваново",
                             "region_code": "ivanovo"}}
    )

    class _Frozen(datetime):
        """datetime с замороженным now(); strptime/конструктор — как есть."""

        @classmethod
        def now(cls, tz=None):
            return _NOW

    monkeypatch.setattr(wr, "datetime", _Frozen)
    return devices_db


def _insert_forecast(db, date, code=0):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute(
        "INSERT OR REPLACE INTO weather_forecast "
        "(forecast_date, weather_code, temp_min, temp_max, fetched_at) "
        "VALUES (?, ?, 1.0, 2.0, ?)",
        (date, code, _NOW.isoformat(timespec="minutes")),
    )
    con.commit()
    con.close()


def test_forecast_excludes_past_dates(wdb):
    today = _NOW.strftime("%Y-%m-%d")
    for delta in (-3, -1, 0, 1, 2, 8):
        _insert_forecast(wdb, (_NOW + timedelta(days=delta))
                         .strftime("%Y-%m-%d"))

    rows = wr.weather_forecast()
    dates = [r[0] for r in rows]

    assert dates[0] == today
    assert all(d >= today for d in dates)
    assert len(dates) == 4  # today, +1, +2 (хвосты отфильтрованы, +8 в пределах 7)


def test_forecast_limit_seven(wdb):
    for delta in range(9):
        _insert_forecast(wdb, (_NOW + timedelta(days=delta))
                         .strftime("%Y-%m-%d"))

    rows = wr.weather_forecast()
    assert len(rows) == 7


def _insert_weather_alert(db, fetched_at, alert="Туман"):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("DELETE FROM weather_alerts")
    con.execute(
        "INSERT INTO weather_alerts (id, fetched_at, region, alert, source_window) "
        "VALUES (1, ?, 'Иваново', ?, 'ближайшие 24 часа')",
        (fetched_at, alert),
    )
    con.commit()
    con.close()


def test_weather_alert_fresh_shown(wdb):
    fresh = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    _insert_weather_alert(wdb, fresh)
    assert wr.weather_alerts() == [
        (fresh, "Иваново", "Туман", "ближайшие 24 часа")
    ]


def test_weather_alert_stale_hidden(wdb):
    stale = (_NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    _insert_weather_alert(wdb, stale)
    assert wr.weather_alerts() == []


def test_weather_alert_empty_hidden(wdb):
    fresh = _NOW.strftime("%Y-%m-%dT%H:%M")
    _insert_weather_alert(wdb, fresh, alert=None)
    assert wr.weather_alerts() == []


def _set_mchs(db, published_at, text, fetched_at=None):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("DELETE FROM mchs_alerts")
    con.execute(
        "INSERT INTO mchs_alerts (id, fetched_at, published_at, title, text, "
        "source_url) VALUES (1, ?, ?, 'Предупреждение', ?, "
        "'https://37.mchs.gov.ru/x')",
        (fetched_at or _NOW.isoformat(timespec="minutes"), published_at, text),
    )
    con.commit()
    con.close()


def test_mchs_expired_text_hidden(wdb):
    _set_mchs(wdb, "2026-09-28 12:07",
              "Действует до 09:00 28 сентября 2026 года")
    assert wr.mchs_alerts() == []


def test_mchs_old_without_expiry_hidden(wdb):
    old = (_NOW - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    _set_mchs(wdb, old, "Гроза, ливень, град")
    assert wr.mchs_alerts() == []


def test_mchs_recent_without_expiry_shown(wdb):
    now_str = _NOW.strftime("%Y-%m-%d %H:%M")
    _set_mchs(wdb, now_str, "Гроза, ливень, град")
    assert len(wr.mchs_alerts()) == 1


def test_mchs_future_expiry_shown_regardless_of_age(wdb):
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    future = _NOW + timedelta(days=1)
    old = (_NOW - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    text = "Действует до 09:00 %d %s %d года" % (
        future.day, months[future.month - 1], future.year)
    _set_mchs(wdb, old, text)
    assert len(wr.mchs_alerts()) == 1


def test_weather_alert_status_shown_when_no_alert(wdb):
    fresh = _NOW.strftime("%Y-%m-%dT%H:%M")
    _insert_weather_alert(wdb, fresh, alert=None)
    assert wr.weather_alerts() == []
    status = wr.weather_alert_status()
    assert status is not None
    assert status[0] == fresh
    assert status[1] == "Иваново"


def test_weather_alert_status_stale_hidden(wdb):
    stale = (_NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    _insert_weather_alert(wdb, stale, alert=None)
    assert wr.weather_alert_status() is None


def test_mchs_alert_status_fresh_even_if_article_expired(wdb):
    _set_mchs(wdb, "2026-09-28 12:07",
              "Действует до 09:00 28 сентября 2026 года")
    assert wr.mchs_alerts() == []
    assert wr.mchs_alert_status() is not None


def test_mchs_alert_status_stale_hidden(wdb):
    stale = (_NOW - timedelta(days=3)).isoformat(timespec="minutes")
    _set_mchs(wdb, "2026-09-28 12:07", "x", fetched_at=stale)
    assert wr.mchs_alert_status() is None


def _insert_env(db, date, radiation, level, points_json=None):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute(
        "INSERT OR REPLACE INTO env_data "
        "(date, uv_index, uv_level, aqi, aqi_level, radiation, "
        "radiation_level, radiation_points, fetched_at) "
        "VALUES (?, 5.0, 'Умеренный', 30, 'Хорошо', ?, ?, ?, ?)",
        (date, radiation, level, points_json,
         _NOW.isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()


def test_load_env_data_radiation_points(wdb):
    pts = [{"name": "Волжская Гмо", "value": 0.12, "dist": 152,
            "dir": "ВЮВ", "date": "03.10.2026", "lat": 56.683, "lng": 43.433},
           {"name": "Павлово", "value": 0.12, "dist": 171,
            "dir": "ЮВ", "date": "03.10.2026", "lat": 55.95, "lng": 43.033}]
    _insert_env(wdb, _NOW.strftime("%Y-%m-%d"), 0.12, "Норма",
                json.dumps(pts, ensure_ascii=False))

    data = wr.load_env_data()
    assert data["radiation"] == 0.12
    assert data["radiation_level"] == "Норма"
    assert [p["name"] for p in data["radiation_points"]] == [
        "Волжская Гмо", "Павлово"]
    assert data["radiation_points"][0]["dist"] == 152
    assert data["settlement"] == "Иваново"


def test_load_env_data_broken_points_json(wdb):
    _insert_env(wdb, _NOW.strftime("%Y-%m-%d"), 0.11, "Норма", "{bad json")

    data = wr.load_env_data()
    assert data["radiation"] == 0.11
    assert data["radiation_points"] is None


def test_load_env_data_old_schema_without_points(wdb):
    import sqlite3
    con = sqlite3.connect(wdb)
    con.execute("ALTER TABLE env_data RENAME TO env_data_new")
    con.execute(
        "CREATE TABLE env_data ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT UNIQUE, "
        "uv_index REAL, uv_level TEXT, aqi REAL, aqi_level TEXT, "
        "pm25 REAL, pm10 REAL, radiation REAL, radiation_level TEXT, "
        "fetched_at TEXT)")
    con.execute(
        "INSERT INTO env_data (date, uv_index, uv_level, radiation, "
        "radiation_level, fetched_at) VALUES (?, 4.0, 'Умеренный', "
        "0.10, 'Норма', ?)",
        (_NOW.strftime("%Y-%m-%d"),
         _NOW.isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()

    data = wr.load_env_data()
    assert data["radiation"] == 0.10
    assert data["radiation_points"] is None
    assert data["settlement"] == "Иваново"

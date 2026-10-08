# -*- coding: utf-8 -*-
"""DB layer (PHASE 2.0-10): владение схемой sqlite devices.db (спека §19/§32).

Проблема 1.1: схема/коннектор жили в modules/devices_routes, а core
(discovery/events/jobs) импортировали их из модуля — инверсия зависимостей
(core -> modules). Здесь: DB-путь, миграции (PRAGMA user_version),
_ensure_extra_tables (9 серверных таблиц), init_db_schema, get_db.

Обратная совместимость: modules/devices_routes реэкспортирует эти символы
(старые импорты app/system_routes/tests продолжают работать).
"""
import sqlite3
import threading
import logging

from core import config

log = logging.getLogger("lan-discovery")

# Task4: путь БД — из core.config (§20/§25): env LAN_PREFIX либо каталог
# самого кода → ./install.sh --prefix DIR кладёт devices.db в свой префикс
# и не пишет в чужой /opt (раньше хардкод ломал изоляцию префикса)
DB = config.DB_PATH

SCHEMA_VERSION = 3
_init_lock = threading.Lock()
_init_done = False


def _migration_v1(con):
    """Миграция 0 → 1: базовая схема devices/events (P1-7)."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            ip TEXT PRIMARY KEY,
            online INTEGER DEFAULT 0,
            mac TEXT,
            vendor TEXT,
            first_seen TEXT,
            last_seen TEXT,
            appearances INTEGER DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ip TEXT,
            hostname TEXT,
            mac TEXT,
            event TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_ip_id ON events(ip, id)"
    )


def _migration_v2(con):
    """Миграция 1 → 2: колонки devices + события v2 (P7-1, P5-25)."""
    columns = {
        row[1]
        for row in con.execute("PRAGMA table_info(devices)").fetchall()
    }
    for col, stmt in (
        ("hostname", "ALTER TABLE devices ADD COLUMN hostname TEXT"),
        ("is_new", "ALTER TABLE devices ADD COLUMN is_new INTEGER DEFAULT 0"),
        ("appearances",
         "ALTER TABLE devices ADD COLUMN appearances INTEGER DEFAULT 0"),
        ("misses", "ALTER TABLE devices ADD COLUMN misses INTEGER DEFAULT 0"),
        ("name", "ALTER TABLE devices ADD COLUMN name TEXT"),
        ("device_type", "ALTER TABLE devices ADD COLUMN device_type TEXT"),
    ):
        if col not in columns:
            con.execute(stmt)

    event_columns = {
        row[1]
        for row in con.execute("PRAGMA table_info(events)").fetchall()
    }
    for col, stmt in (
        ("severity", "ALTER TABLE events ADD COLUMN severity TEXT"),
        ("source", "ALTER TABLE events ADD COLUMN source TEXT"),
        ("metadata", "ALTER TABLE events ADD COLUMN metadata TEXT"),
    ):
        if col not in event_columns:
            con.execute(stmt)

    # backfill старых строк (идемпотентно, только NULL)
    con.execute(
        "UPDATE events SET severity='info' WHERE severity IS NULL "
        "AND event IN ('NEW', 'ONLINE')"
    )
    con.execute(
        "UPDATE events SET severity='warning' WHERE severity IS NULL "
        "AND event IN ('OFFLINE', 'MAC_CHANGED')"
    )
    con.execute("UPDATE events SET severity='info' WHERE severity IS NULL")
    con.execute("UPDATE events SET source='discovery' WHERE source IS NULL")

    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_event ON events(event, id)"
    )


def _migration_v3(con):
    """Миграция 2 → 3: явный device_id + таблица ip_history (спека §15).

    device_id — стабильный идентификатор устройства (MAC-производный,
    fallback на IP), НЕ переписывается при смене MAC (§15: device_id
    первичен, MAC → hostname → IP history). Строки-переезды (один MAC
    на разных IP) наследуют общий device_id. ip_history — цепочка IP.
    """
    columns = {
        row[1]
        for row in con.execute("PRAGMA table_info(devices)").fetchall()
    }
    if "device_id" not in columns:
        con.execute("ALTER TABLE devices ADD COLUMN device_id TEXT")
    con.execute("""
        CREATE TABLE IF NOT EXISTS ip_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            ip TEXT NOT NULL,
            first_seen TEXT,
            last_seen TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_history_device "
        "ON ip_history(device_id, ip)"
    )
    # backfill: mac:xx… для строк с MAC, иначе ip:<ip>; история — все пары
    con.execute("""
        UPDATE devices SET device_id = CASE
            WHEN mac IS NOT NULL AND mac <> ''
                THEN 'mac:' || lower(mac)
            ELSE 'ip:' || ip
        END
        WHERE device_id IS NULL
    """)
    con.execute("""
        INSERT INTO ip_history (device_id, ip, first_seen, last_seen)
        SELECT device_id, ip, first_seen, last_seen FROM devices
        WHERE NOT EXISTS (
            SELECT 1 FROM ip_history h
            WHERE h.device_id = devices.device_id AND h.ip = devices.ip
        )
    """)


# Нумерованные шаги: применяются строго по PRAGMA user_version,
# каждый шаг переводит схему на следующую версию (P5-25).
MIGRATIONS = (
    (1, _migration_v1),
    (2, _migration_v2),
    (3, _migration_v3),
)


def _ensure_extra_tables(con):
    """Таблицы, CREATE которых жил только на серверах (P5-26).

    Точный DDL из боевой БД: панель их читает (weather_routes/app.py),
    пишет deploy/weather-update.py; без ensure восстановление на чистой
    системе даёт неполную схему. Плюс jobs (2.0-3) — DDL живёт в
    core/jobs.py (JOBS_DDL), здесь только вызов.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS env_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            uv_index REAL,
            uv_level TEXT,
            aqi REAL,
            aqi_level TEXT,
            pm25 REAL,
            pm10 REAL,
            radiation REAL,
            radiation_level TEXT,
            radiation_points TEXT,
            fetched_at TEXT
        )
    """)
    _env_cols = {r[1] for r in con.execute("PRAGMA table_info(env_data)")}
    if "radiation_points" not in _env_cols:
        con.execute("ALTER TABLE env_data ADD COLUMN radiation_points TEXT")
    con.execute("""
        CREATE TABLE IF NOT EXISTS mchs_alerts (
            id INTEGER PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            published_at TEXT,
            title TEXT NOT NULL,
            text TEXT,
            source_url TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_alerts (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            fetched_at TEXT NOT NULL,
            region TEXT NOT NULL,
            alert TEXT,
            source_window TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_daily (
            date TEXT PRIMARY KEY,
            temp_min REAL,
            temp_max REAL,
            temp_avg REAL,
            humidity_avg REAL,
            pressure_avg REAL,
            wind_avg REAL,
            precipitation_sum REAL,
            observations INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_forecast (
            forecast_date TEXT PRIMARY KEY,
            weather_code INTEGER,
            temp_min REAL,
            temp_max REAL,
            precipitation_sum REAL,
            precipitation_probability INTEGER,
            wind_speed_max REAL,
            wind_direction INTEGER,
            sunrise TEXT,
            sunset TEXT,
            fetched_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_forecast_history (
            fetched_at TEXT NOT NULL,
            forecast_date TEXT NOT NULL,
            weather_code INTEGER,
            temp_min REAL,
            temp_max REAL,
            precipitation_sum REAL,
            precipitation_probability INTEGER,
            wind_speed_max REAL,
            wind_direction INTEGER,
            sunrise TEXT,
            sunset TEXT,
            PRIMARY KEY (fetched_at, forecast_date)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_hourly (
            forecast_date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            temperature REAL,
            weather_code INTEGER,
            precipitation REAL,
            precipitation_probability REAL,
            wind_speed REAL,
            wind_direction INTEGER,
            cloud_cover REAL,
            PRIMARY KEY (forecast_date, hour)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS weather_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temperature REAL,
            apparent_temperature REAL,
            humidity REAL,
            precipitation REAL,
            weather_code INTEGER,
            wind_speed REAL,
            wind_direction INTEGER,
            pressure REAL,
            cloud_cover REAL
        )
    """)
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_weather_observations_timestamp_unique "
        "ON weather_observations(timestamp)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS motion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            camera_id INTEGER NOT NULL,
            camera_name TEXT,
            score REAL,
            detector TEXT,
            photo TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_motion_events_epoch "
        "ON motion_events(camera_id, epoch)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS motion_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            created_epoch INTEGER NOT NULL,
            attempts INTEGER DEFAULT 0,
            next_epoch INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            last_error TEXT,
            sent_epoch INTEGER,
            caption TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_motion_queue_status "
        "ON motion_queue(status, next_epoch)"
    )
    _mq_cols = {r[1] for r in con.execute("PRAGMA table_info(motion_queue)")}
    if "caption" not in _mq_cols:
        con.execute("ALTER TABLE motion_queue ADD COLUMN caption TEXT")

    from core.jobs import ensure_jobs_table
    ensure_jobs_table(con)

    from core.automation import ensure_automation_table
    ensure_automation_table(con)


def _retention_days():
    return int(config.get("events", "retention_days", 180) or 0)


def init_db_schema(force=False):
    """Однократная инициализация/миграция схемы (P1-7, P5-25/26/27).

    - шаги MIGRATIONS применяются строго по PRAGMA user_version;
    - _ensure_extra_tables — идемпотентный CREATE 9 «серверных» таблиц;
    - retention events/jobs (events.retention_days) при старте;
    - jobs, оборванные рестартом, → failed (2.0-3).
    """
    global _init_done
    with _init_lock:
        if _init_done and not force:
            return False
        con = sqlite3.connect(DB, timeout=30)
        try:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA journal_mode=WAL")

            version = con.execute("PRAGMA user_version").fetchone()[0]
            for ver, step in MIGRATIONS:
                if version < ver:
                    step(con)
                    con.execute(f"PRAGMA user_version = {ver}")
                    version = ver
                    log.info(f"DB MIGRATION: applied v{ver}")

            _ensure_extra_tables(con)

            try:
                from core.events import cleanup_old_events
                cleanup_old_events(con, _retention_days())
            except Exception as e:
                log.error(f"EVENTS RETENTION ERROR: {e}")

            try:
                from core.jobs import cleanup_old_jobs, recover_interrupted
                recover_interrupted(con)
                cleanup_old_jobs(con, _retention_days())
            except Exception as e:
                log.error(f"JOBS RETENTION ERROR: {e}")

            con.commit()
            _init_done = True
            log.info(f"DB SCHEMA INIT: version={SCHEMA_VERSION}")
            return True
        finally:
            con.close()


def get_db():
    if not _init_done:
        init_db_schema()
    con = sqlite3.connect(
        DB,
        timeout=30
    )

    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")

    return con

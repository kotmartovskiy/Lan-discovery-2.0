import sqlite3
import threading
import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for, jsonify

log = logging.getLogger("lan-discovery")

DB = "/opt/lan-discovery/devices.db"

# Discovery engine (PHASE 6): движок вынесен в core/discovery.py.
# Реэкспорт — обратная совместимость: app.py / system_routes импортируют
# эти символы из devices_routes; reconcile нужен и job'е скана (2.0-3).
from core.discovery import (  # noqa: F401
    get_hostname,
    get_scan_status,
    parse_scan,
    reconcile,
    run_scan,
    scan_loop,
    start_scan_thread,
)

SCHEMA_VERSION = 2
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


# Нумерованные шаги: применяются строго по PRAGMA user_version,
# каждый шаг переводит схему на следующую версию (P5-25).
MIGRATIONS = (
    (1, _migration_v1),
    (2, _migration_v2),
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
            fetched_at TEXT
        )
    """)
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

    from core.jobs import ensure_jobs_table
    ensure_jobs_table(con)


def _retention_days():
    from app import _cfg
    return int(_cfg("events", "retention_days", 180) or 0)


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


def register_routes(app):
    from modules.auth import login_required, can_edit, admin_required

    @app.route("/")
    @login_required
    def index():

        con = get_db()
        try:
            devices = con.execute(
                """
                SELECT
                    ip,
                    online,
                    name,
                    hostname,
                    mac,
                    vendor,
                    first_seen,
                    last_seen,
                    misses,
                    appearances,
                    is_new,
                    device_type
                FROM devices

                ORDER BY
                    is_new DESC,
                    online DESC,
                    ip
                """
            ).fetchall()

            total = len(devices)

            online = sum(
                1
                for d in devices
                if d[1]
            )
        finally:
            con.close()

        prepared = []

        for d in devices:

            prepared.append(
                (
                    d[0],
                    d[1],
                    d[2],
                    d[3],
                    d[4],
                    d[5],
                    d[6],
                    d[7],
                    d[8],
                    d[9],
                    d[10],
                    d[11]
                )
            )

        from app import page_data
        data = page_data()

        return render_template("devices.html",
            devices=prepared,
            total=total,
            online=online,
            **data
        )

    @app.route("/history")
    @login_required
    def history():

        con = get_db()
        try:
            events = con.execute(
                """
                SELECT
                    timestamp,
                    ip,
                    hostname,
                    mac,
                    event,
                    severity

                FROM events

                ORDER BY id DESC

                LIMIT 500
                """
            ).fetchall()
        finally:
            con.close()

        from app import page_data
        data = page_data()

        return render_template("history.html",
            events=events,
            **data
        )

    @app.route("/device/<ip>")
    @login_required
    def device(ip):

        con = get_db()
        try:
            device = con.execute(
                """
                SELECT
                    ip,
                    online,
                    name,
                    hostname,
                    mac,
                    vendor,
                    first_seen,
                    last_seen,
                    misses,
                    appearances,
                    is_new,
                    device_type
                FROM devices
                WHERE ip=?
                """,
                (ip,)
            ).fetchone()

            if not device:

                return "Устройство не найдено", 404

            events = con.execute(
                """
                SELECT
                    timestamp,
                    event,
                    severity

                FROM events

                WHERE ip=?

                ORDER BY id DESC

                LIMIT 100
                """,
                (ip,)
            ).fetchall()
        finally:
            con.close()

        try:
            from modules.inventory import get_inventory
            inventory = get_inventory(ip) or {}
        except Exception:
            inventory = {}

        from app import page_data
        data = page_data()

        return render_template("device.html",
            device=device,
            events=events,
            inventory=inventory,
            **data
        )

    @app.route("/device/<ip>/name", methods=["POST"])
    @can_edit
    @login_required
    def set_name(ip):

        name = request.form.get(
            "name",
            ""
        ).strip()

        device_type = request.form.get(
            "device_type",
            ""
        ).strip()

        con = get_db()
        try:
            con.execute(
                """
                UPDATE devices
                SET name=?, device_type=?
                WHERE ip=?
                """,
                (
                    name if name else None,
                    device_type if device_type else None,
                    ip
                )
            )

            con.commit()
        finally:
            con.close()

        return redirect(
            url_for(
                "device",
                ip=ip
            )
        )

    @app.route("/api/device/<ip>/dismiss-new", methods=["POST"])
    @can_edit
    @login_required
    def dismiss_new(ip):
        con = get_db()
        try:
            con.execute("UPDATE devices SET is_new=0 WHERE ip=?", (ip,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @app.route("/api/events")
    @login_required
    def api_events():
        """Фильтрованная лента событий (P7-3): ?limit=&event=&severity=&ip=."""
        from core.events import list_events, event_to_dict

        try:
            limit = max(1, min(2000, int(request.args.get("limit", 500))))
        except (TypeError, ValueError):
            limit = 500

        con = get_db()
        try:
            rows = list_events(
                con,
                limit=limit,
                event=request.args.get("event") or None,
                severity=request.args.get("severity") or None,
                ip=request.args.get("ip") or None,
                source=request.args.get("source") or None,
            )
        finally:
            con.close()

        return jsonify({"ok": True, "events": [event_to_dict(r)
                                               for r in rows]})

    @app.route("/api/scan", methods=["POST"])
    @admin_required
    def api_scan():
        """Ручное сканирование (P6-2; 2.0-3 — job, one-shot, без settings).

        Тело (JSON, опционально): {"subnet": "192.168.1.0/24",
        "ifaces": ["eth0"]}. Без параметров — текущая конфигурация.
        Ответ: {"ok": true, "job": "<id>"} — статус и результат задачи:
        GET /api/jobs/<id> (аддитивно к 1.1: devices/stats/subnet теперь
        в job.result; ошибка nmap — статус job=failed).
        """
        data = request.get_json(silent=True) or {}
        subnet = (data.get("subnet") or "").strip() or None
        ifaces = data.get("ifaces")
        if ifaces is not None and (
            not isinstance(ifaces, list)
            or not ifaces
            or not all(isinstance(i, str) and i.strip() for i in ifaces)
        ):
            return jsonify(
                {"ok": False, "error": "ifaces: непустой список строк"}
            ), 400
        if ifaces:
            ifaces = [i.strip() for i in ifaces]

        from core import jobs
        jid = jobs.submit(
            "network-scan",
            lambda ctx: _scan_job(ctx, subnet, ifaces),
            meta={"subnet": subnet or "авто"},
        )
        return jsonify({"ok": True, "job": jid})


def _current_subnet():
    from app import _cfg
    return _cfg("network", "subnet", "192.168.3.0/24")


def _scan_job(ctx, subnet, ifaces):
    """JOB (2.0-3): ручной скан — nmap + reconcile в фоне.

    cancelable не ставим: nmap-прогон изнутри не прервать (каждый ≤45s,
    полный скан — несколько прогонов), честной отмены посреди нет.
    """
    ctx.log("Скан: запуск nmap (%s)" % (subnet or "текущая подсеть"))
    ctx.progress(10)
    out = run_scan(subnet=subnet, ifaces=ifaces)
    if out is None:
        raise RuntimeError(
            "сканирование недоступно (nmap отсутствует "
            "или все прогоны упали)"
        )
    ctx.progress(60)
    current = parse_scan(out)
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    con = get_db()
    try:
        stats = reconcile(con, current, now)
        con.commit()
    finally:
        con.close()

    ctx.progress(100)
    ctx.log("Скан завершён: %d устройств" % len(current))
    return {
        "devices": len(current),
        "stats": stats,
        "subnet": subnet or _current_subnet(),
    }

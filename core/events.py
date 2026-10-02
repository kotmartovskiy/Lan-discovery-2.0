# -*- coding: utf-8 -*-
"""Event engine (PHASE 7): формализованные события, единая точка записи/чтения.

Схема events v2 (см. core/db.init_db_schema): колонки
`severity` (info/warning/critical), `source` (discovery/system/monitoring/
user), `metadata` (JSON-текст).

Фабрика `add_event` — единственная точка INSERT: тип события неизвестный
получает severity=info; известные — из `EVENT_SEVERITY`.
"""
import json
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("lan-discovery")

EVENT_SEVERITY = {
    "NEW": "info",
    "ONLINE": "info",
    "OFFLINE": "warning",
    "MAC_CHANGED": "warning",
    "IP_CHANGED": "info",
}
DEFAULT_SEVERITY = "info"
SEVERITIES = ("info", "warning", "critical")


def now_ts():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def add_event(con, ip, hostname=None, mac=None, event="INFO",
              source="discovery", metadata=None, severity=None,
              timestamp=None):
    """Вставить событие; возвращает фактическую severity."""
    if severity not in SEVERITIES:
        severity = EVENT_SEVERITY.get(event, DEFAULT_SEVERITY)
    meta = json.dumps(metadata, ensure_ascii=False) if metadata else None
    con.execute(
        """
        INSERT INTO events
        (timestamp, ip, hostname, mac, event, severity, source, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (timestamp or now_ts(), ip, hostname, mac, event, severity,
         source, meta),
    )
    return severity


def list_events(con, limit=500, event=None, severity=None, ip=None,
                source=None):
    """SELECT событий с фильтрами; новые первыми."""
    q = ("SELECT id, timestamp, ip, hostname, mac, event, severity, source, "
         "metadata FROM events WHERE 1=1")
    params = []
    if event:
        q += " AND event=?"
        params.append(event)
    if severity:
        q += " AND severity=?"
        params.append(severity)
    if ip:
        q += " AND ip=?"
        params.append(ip)
    if source:
        q += " AND source=?"
        params.append(source)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return con.execute(q, params).fetchall()


def event_to_dict(row):
    """Строка list_events -> dict (metadata парсится из JSON)."""
    meta = row[8]
    if meta:
        try:
            meta = json.loads(meta)
        except Exception:
            pass
    return {
        "id": row[0],
        "timestamp": row[1],
        "ip": row[2],
        "hostname": row[3],
        "mac": row[4],
        "event": row[5],
        "severity": row[6],
        "source": row[7],
        "metadata": meta,
    }


def cleanup_old_events(con, days):
    """Удалить события старше days дней (P5-27).

    timestamp в формате DD.MM.YYYY HH:MM:SS — парсится в Python, удаление
    батчами по id. days <= 0 → no-op (retention выключен). Коммитит сама.
    """
    if not days or int(days) <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=int(days))

    stale = []
    for row in con.execute("SELECT id, timestamp FROM events"):
        try:
            ts = datetime.strptime(row[1], "%d.%m.%Y %H:%M:%S")
        except Exception:
            continue
        if ts < cutoff:
            stale.append(row[0])

    removed = 0
    for i in range(0, len(stale), 500):
        batch = stale[i:i + 500]
        cur = con.execute(
            "DELETE FROM events WHERE id IN (%s)"
            % ",".join("?" * len(batch)),
            batch,
        )
        removed += cur.rowcount
    if removed:
        con.commit()
        log.info(f"EVENTS RETENTION: removed {removed} events "
                 f"older than {days} days")
    return removed


def retention_loop(interval=86400):
    """Ежедневная чистка events по events.retention_days (P5-27)."""
    while True:
        time.sleep(interval)
        try:
            from core import config
            days = int(config.get("events", "retention_days", 180) or 0)
            if days <= 0:
                continue
            from core.db import get_db
            con = get_db()
            try:
                cleanup_old_events(con, days)
            finally:
                con.close()
        except Exception as e:
            log.error(f"EVENTS RETENTION ERROR: {e}")

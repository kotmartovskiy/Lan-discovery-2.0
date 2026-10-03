# -*- coding: utf-8 -*-
"""Event engine (PHASE 7 + 2.0-12): события + namespace-имена §16.

Схема events v2 (см. core/db.init_db_schema): колонки
`severity` (info/warning/critical), `source` (discovery/system/monitoring/
user), `metadata` (JSON-текст).

Фабрика `add_event` — единственная точка INSERT: тип события неизвестный
получает severity=info; известные — из `EVENT_SEVERITY`.

PHASE 2.0-12 (§16 Event system):
- `NAMESPACE_EVENTS` — канонические имена device./network./storage./
  camera./job./module./system. (строгие для `emit`);
- `emit()` — запись + fan-out подписчикам (фундамент Automation §17);
- `subscribe()` — in-process подписка, ошибки подписчиков не роняют писателя;
- dual-read в `list_events`: фильтр `event=ONLINE` видит и legacy, и
  `device.online` (и обратно) — лента/UI не ломаются.

Семантика доставки (зафиксировано, §16/§17, docs/Архитектура-2.0 §3.11):
*durable history + best-effort in-process notify*. SQLite-запись
переживает рестарт, но notify делает только живой процесс (после commit:
`emit` — сразу, discovery — `notify_all`); kill -9 в этом окне теряет
доставку, не запись. Replay/cursor нет — рестарт историю не переигрывает;
at-most-once на событие, порядок = порядок пачки.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger("lan-discovery")

# канонические имена §16: emit() принимает ТОЛЬКО их (новые имена
# добавляются сюда), severity по умолчанию для каждого
NAMESPACE_EVENTS = {
    "device.new": "info",
    "device.online": "info",
    "device.offline": "warning",
    "device.ip_changed": "info",
    "device.mac_changed": "warning",
    "network.link_up": "info",
    "network.link_down": "warning",
    "storage.inserted": "info",
    "storage.removed": "warning",
    "storage.health_changed": "warning",
    "camera.motion": "info",
    "job.started": "info",
    "job.completed": "info",
    "job.failed": "critical",
    "job.cancelled": "warning",
    "module.installed": "info",
    "module.updated": "info",
    "module.failed": "critical",
    "system.warning": "warning",
    "system.error": "critical",
}

# legacy-имена (первая модель events, PHASE 7) <-> namespace (dual-read)
LEGACY_ALIASES = {
    "NEW": "device.new",
    "ONLINE": "device.online",
    "OFFLINE": "device.offline",
    "MAC_CHANGED": "device.mac_changed",
    "IP_CHANGED": "device.ip_changed",
}
REVERSE_ALIASES = {v: k for k, v in LEGACY_ALIASES.items()}

EVENT_SEVERITY = {
    "NEW": "info",
    "ONLINE": "info",
    "OFFLINE": "warning",
    "MAC_CHANGED": "warning",
    "IP_CHANGED": "info",
    **NAMESPACE_EVENTS,
}
DEFAULT_SEVERITY = "info"
SEVERITIES = ("info", "warning", "critical")

_SUBSCRIBERS = []
_sub_lock = threading.Lock()


def now_ts():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def add_event(con, ip, hostname=None, mac=None, event="INFO",
              source="discovery", metadata=None, severity=None,
              timestamp=None, out=None):
    """Вставить событие; возвращает фактическую severity.

    out (B-03) — список-сборщик payload'ов: событие копируется туда
    вместо немедленного fan-out, доставка — `notify_all` ПОСЛЕ commit
    вызывающей стороны (иначе подписчик automation пишет в БД внутри
    незакрытой транзакции писателя → busy/deadlock, §16/§17).
    Имя в payload — каноническое namespace-имя (legacy-имя остаётся
    в колонке events.event, dual-read их сводит).
    """
    if severity not in SEVERITIES:
        severity = EVENT_SEVERITY.get(event, DEFAULT_SEVERITY)
    meta = json.dumps(metadata, ensure_ascii=False) if metadata else None
    ts = timestamp or now_ts()
    con.execute(
        """
        INSERT INTO events
        (timestamp, ip, hostname, mac, event, severity, source, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, ip, hostname, mac, event, severity, source, meta),
    )
    if out is not None:
        out.append({
            "name": LEGACY_ALIASES.get(event, event),
            "ip": ip, "hostname": hostname, "mac": mac,
            "severity": severity, "source": source,
            "metadata": metadata, "timestamp": ts,
        })
    return severity


# --- PHASE 2.0-12: namespace-имена, emit/subscribe, dual-read (§16) ---------

def subscribe(handler):
    """Подписка на события: handler(payload_dict) -> None.

    Возвращает unsubscribe-функцию. Ошибки подписчиков логируются и не
    роняют писателя (фундамент Automation, §17).
    """
    with _sub_lock:
        _SUBSCRIBERS.append(handler)

    def unsubscribe():
        with _sub_lock:
            if handler in _SUBSCRIBERS:
                _SUBSCRIBERS.remove(handler)

    return unsubscribe


def _notify(payload):
    with _sub_lock:
        subs = list(_SUBSCRIBERS)
    for handler in subs:
        try:
            handler(payload)
        except Exception as e:
            log.error("EVENTS subscriber error (%s): %s",
                      payload.get("name"), e)


def notify_all(payloads):
    """Отложенный fan-out (B-03): доставить подписчикам payload'ы,
    накопленные `add_event(..., out=...)`.

    Вызывать только ПОСЛЕ commit писателя: подписчик automation
    открывает своё соединение и коммитит записи (fired_count) — внутри
    чужой незакрытой транзакции это busy-ожидание/deadlock (§16/§17).
    """
    for payload in payloads:
        _notify(payload)


def emit(name, *, con=None, ip=None, hostname=None, mac=None,
         severity=None, source="core", metadata=None, timestamp=None):
    """Namespace-событие §16: строгое имя + INSERT в events + подписчики.

    con — готовое соединение (unit-тесты/владелец транзакции); без него
    открывается своё через core.db.get_db. Persist best-effort: ошибка БД
    логируется, писатель и подписчики не падают. Возвращает payload.
    """
    if name not in NAMESPACE_EVENTS:
        raise ValueError(f"неизвестное имя события: {name}")
    if severity not in SEVERITIES:
        severity = NAMESPACE_EVENTS[name]
    ts = timestamp or now_ts()
    payload = {
        "name": name, "ip": ip, "hostname": hostname, "mac": mac,
        "severity": severity, "source": source, "metadata": metadata,
        "timestamp": ts,
    }
    own = con is None
    if own:
        try:
            from core.db import get_db
            con = get_db()
        except Exception as e:
            log.error("EVENTS emit open error %s: %s", name, e)
            con = None
    if con is not None:
        try:
            add_event(con, ip, hostname=hostname, mac=mac, event=name,
                      source=source, metadata=metadata, severity=severity,
                      timestamp=ts)
            con.commit()
        except Exception as e:
            log.error("EVENTS emit persist error %s: %s", name, e)
        finally:
            if own:
                con.close()
    _notify(payload)
    return payload


def _event_variants(event):
    """Фильтр dual-read: legacy <-> namespace (ONLINE ~ device.online)."""
    variants = {event}
    if event in LEGACY_ALIASES:
        variants.add(LEGACY_ALIASES[event])
    if event in REVERSE_ALIASES:
        variants.add(REVERSE_ALIASES[event])
    return sorted(variants)


def list_events(con, limit=500, event=None, severity=None, ip=None,
                source=None):
    """SELECT событий с фильтрами; новые первыми.

    Фильтр event — dual-read (§16): legacy и namespace-имена одного
    события считаются одним фильтром.
    """
    q = ("SELECT id, timestamp, ip, hostname, mac, event, severity, source, "
         "metadata FROM events WHERE 1=1")
    params = []
    if event:
        variants = _event_variants(event)
        q += " AND event IN (%s)" % ",".join("?" * len(variants))
        params.extend(variants)
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

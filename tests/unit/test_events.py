# -*- coding: utf-8 -*-
"""Unit: event engine (PHASE 7 / P5-27 + 2.0-12 §16)."""
import json
from datetime import datetime, timedelta

import pytest

import core.events as ev
from core.events import (EVENT_SEVERITY, NAMESPACE_EVENTS, add_event,
                         cleanup_old_events, emit, event_to_dict,
                         list_events, notify_all, now_ts, subscribe)


@pytest.fixture(autouse=True)
def _clean_subscribers():
    """глобальный список подписчиков не течёт между тестами."""
    yield
    ev._SUBSCRIBERS.clear()


def test_now_ts_format():
    datetime.strptime(now_ts(), "%d.%m.%Y %H:%M:%S")


def test_severity_map():
    assert EVENT_SEVERITY["NEW"] == "info"
    assert EVENT_SEVERITY["OFFLINE"] == "warning"
    assert EVENT_SEVERITY["MAC_CHANGED"] == "warning"


def test_add_event_severity_resolution(events_con):
    assert add_event(events_con, "10.0.0.1", event="NEW") == "info"
    assert add_event(events_con, "10.0.0.2", event="OFFLINE") == "warning"
    assert add_event(events_con, "10.0.0.3", event="ANYTHING") == "info"
    assert add_event(events_con, "10.0.0.4", event="NEW",
                     severity="critical") == "critical"
    events_con.commit()


def test_add_event_persists_columns(events_con):
    add_event(events_con, "10.0.0.5", hostname="h1", mac="AA:BB:CC:DD:EE:FF",
              event="NEW", source="discovery", metadata={"x": 1},
              timestamp="01.02.2026 12:00:00")
    events_con.commit()
    row = list_events(events_con, ip="10.0.0.5")[0]
    d = event_to_dict(row)
    assert d["hostname"] == "h1"
    assert d["mac"] == "AA:BB:CC:DD:EE:FF"
    assert d["severity"] == "info"
    assert d["source"] == "discovery"
    assert d["metadata"] == {"x": 1}
    assert d["timestamp"] == "01.02.2026 12:00:00"


def test_list_events_filters(events_con):
    add_event(events_con, "10.0.0.1", event="NEW")
    add_event(events_con, "10.0.0.2", event="OFFLINE")
    add_event(events_con, "10.0.0.2", event="ONLINE", source="user")
    events_con.commit()
    assert len(list_events(events_con)) == 3
    assert len(list_events(events_con, event="OFFLINE")) == 1
    assert len(list_events(events_con, ip="10.0.0.2")) == 2
    assert len(list_events(events_con, severity="warning")) == 1
    assert len(list_events(events_con, source="user")) == 1
    assert len(list_events(events_con, limit=2)) == 2


def test_list_events_order_new_first(events_con):
    add_event(events_con, "10.0.0.1", event="NEW")
    add_event(events_con, "10.0.0.2", event="ONLINE")
    events_con.commit()
    rows = list_events(events_con)
    assert rows[0][2] == "10.0.0.2"


def test_event_to_dict_bad_metadata(events_con):
    add_event(events_con, "10.0.0.9", event="NEW")
    events_con.commit()
    events_con.execute(
        "UPDATE events SET metadata=? WHERE ip=?",
        ("{broken", "10.0.0.9"),
    )
    row = list_events(events_con, ip="10.0.0.9")[0]
    assert event_to_dict(row)["metadata"] == "{broken"


def test_cleanup_removes_old_keeps_fresh(events_con):
    old = (datetime.now() - timedelta(days=400)).strftime("%d.%m.%Y %H:%M:%S")
    fresh = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    events_con.execute("INSERT INTO events (timestamp, ip, event) VALUES (?,?,?)",
                       (old, "10.0.0.1", "NEW"))
    events_con.execute("INSERT INTO events (timestamp, ip, event) VALUES (?,?,?)",
                       (fresh, "10.0.0.2", "NEW"))
    events_con.commit()
    assert cleanup_old_events(events_con, 180) == 1
    left = [r[0] for r in events_con.execute("SELECT ip FROM events")]
    assert left == ["10.0.0.2"]


def test_cleanup_disabled_and_garbage(events_con):
    old = (datetime.now() - timedelta(days=400)).strftime("%d.%m.%Y %H:%M:%S")
    events_con.execute("INSERT INTO events (timestamp, ip, event) VALUES (?,?,?)",
                       (old, "10.0.0.1", "NEW"))
    events_con.execute("INSERT INTO events (timestamp, ip, event) VALUES (?,?,?)",
                       ("not-a-date", "10.0.0.2", "NEW"))
    events_con.commit()
    assert cleanup_old_events(events_con, 0) == 0
    assert cleanup_old_events(events_con, None) == 0
    n = events_con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert n == 2
    # мусорная дата не роняет чистку и не удаляется
    assert cleanup_old_events(events_con, 180) == 1
    assert events_con.execute(
        "SELECT ip FROM events").fetchone()[0] == "10.0.0.2"


def test_metadata_json_roundtrip(events_con):
    add_event(events_con, "10.0.0.7", event="NEW",
              metadata={"list": [1, 2], "с": "юникод"})
    events_con.commit()
    d = event_to_dict(list_events(events_con, ip="10.0.0.7")[0])
    assert d["metadata"] == {"list": [1, 2], "с": "юникод"}
    raw = events_con.execute("SELECT metadata FROM events").fetchone()[0]
    assert json.loads(raw) == d["metadata"]


# --- PHASE 2.0-12: namespace-имена, emit/subscribe, dual-read (§16) --------

def test_namespace_events_registered():
    assert NAMESPACE_EVENTS["device.online"] == "info"
    assert NAMESPACE_EVENTS["job.failed"] == "critical"
    assert NAMESPACE_EVENTS["system.error"] == "critical"
    # namespace-имена видит и старый add_event (severity из той же карты)
    assert EVENT_SEVERITY["device.offline"] == "warning"


def test_emit_strict_name(events_con):
    with pytest.raises(ValueError):
        emit("not-a-namespace", con=events_con)


def test_emit_persists_strict_name(events_con):
    payload = emit("job.completed", con=events_con, ip="10.0.0.5",
                   source="jobs", metadata={"job_id": "a1"})
    events_con.commit()
    assert payload["severity"] == "info"
    d = event_to_dict(list_events(events_con, ip="10.0.0.5")[0])
    assert d["event"] == "job.completed"
    assert d["severity"] == "info"
    assert d["source"] == "jobs"
    assert d["metadata"] == {"job_id": "a1"}


def test_emit_severity_defaults(events_con):
    assert emit("system.error", con=events_con, ip="10.0.0.6")[
        "severity"] == "critical"
    assert emit("device.offline", con=events_con, ip="10.0.0.6")[
        "severity"] == "warning"
    # явный severity перебивает канонический
    assert emit("job.failed", con=events_con, ip="10.0.0.6",
                severity="warning")["severity"] == "warning"
    events_con.commit()
    sev = [r[6] for r in
           list_events(events_con, ip="10.0.0.6")]
    assert sev == ["warning", "warning", "critical"]  # новые первыми


def test_emit_subscribe_unsubscribe(events_con):
    seen = []
    unsub = subscribe(lambda p: seen.append(p["name"]))
    emit("camera.motion", con=events_con, ip="10.0.0.8")
    events_con.commit()
    assert seen == ["camera.motion"]
    # payload полон для Automation (§17)
    unsub()
    emit("camera.motion", con=events_con, ip="10.0.0.8")
    events_con.commit()
    assert seen == ["camera.motion"]


def test_emit_payload_shape(events_con):
    got = []
    subscribe(got.append)
    emit("network.link_down", con=events_con, ip="10.0.0.7",
         source="monitoring", metadata={"iface": "eth0"})
    events_con.commit()
    p = got[0]
    assert set(p) == {"name", "ip", "hostname", "mac", "severity",
                      "source", "metadata", "timestamp"}
    assert p["name"] == "network.link_down"
    assert p["severity"] == "warning"
    assert p["metadata"] == {"iface": "eth0"}


def test_emit_survives_broken_subscriber(events_con):
    def boom(p):
        raise RuntimeError("boom")

    subscribe(boom)
    emit("system.warning", con=events_con, ip="10.0.0.9")
    events_con.commit()
    # событие записано несмотря на сломанного подписчика
    assert list_events(events_con, ip="10.0.0.9")[0][5] == "system.warning"


def test_list_events_dual_read_aliases(events_con):
    add_event(events_con, "10.0.0.1", event="ONLINE")
    add_event(events_con, "10.0.0.2", event="device.online")
    add_event(events_con, "10.0.0.3", event="OFFLINE")
    events_con.commit()
    # legacy-фильтр видит namespace-события и наоборот (и только свои)
    assert len(list_events(events_con, event="ONLINE")) == 2
    assert len(list_events(events_con, event="device.online")) == 2
    assert len(list_events(events_con, event="OFFLINE")) == 1
    assert len(list_events(events_con, event="device.offline")) == 1
    # события без алиаса не смешиваются
    add_event(events_con, "10.0.0.4", event="job.started")
    events_con.commit()
    assert len(list_events(events_con, event="job.started")) == 1
    assert len(list_events(events_con, event="ONLINE")) == 2
# --- B-03: отложенный fan-out discovery -> Automation (§16/§17) ------------

def test_add_event_out_defers_notify(events_con):
    """add_event(out=...) копирует payload, доставка — только notify_all."""
    seen = []
    subscribe(seen.append)
    out = []
    add_event(events_con, "10.0.0.1", hostname="h", event="OFFLINE",
              metadata={"x": 1}, timestamp="01.02.2026 12:00:00", out=out)
    # подписчик молчит до fan-out (тот вызывается после commit писателя)
    assert seen == []
    assert len(out) == 1
    p = out[0]
    # имя payload — каноническое namespace-имя (dual-read сводит с legacy)
    assert p == {"name": "device.offline", "ip": "10.0.0.1",
                 "hostname": "h", "mac": None, "severity": "warning",
                 "source": "discovery", "metadata": {"x": 1},
                 "timestamp": "01.02.2026 12:00:00"}
    events_con.commit()
    notify_all(out)
    assert seen == [p]
    # в БД событие записано legacy-именем (обратная совместимость UI/фильтров)
    assert event_to_dict(list_events(events_con, ip="10.0.0.1")[0])[
        "event"] == "OFFLINE"


def test_add_event_without_out_no_fanout(events_con):
    """Без out фан-аут не делает ни add_event, ни notify_all (это emit)."""
    seen = []
    subscribe(seen.append)
    add_event(events_con, "10.0.0.2", event="NEW")
    events_con.commit()
    assert seen == []
    assert list_events(events_con, ip="10.0.0.2")[0][5] == "NEW"


# --- Task5: семантика доставки — durable history + best-effort notify (§16) ---

def test_events_persist_but_not_replayed(events_con):
    """«Рестарт» (пустой список подписчиков) не переигрывает историю."""
    # событие уже в истории — переживает рестарт процесса
    add_event(events_con, "10.0.0.1", event="OFFLINE")
    events_con.commit()
    assert len(list_events(events_con, ip="10.0.0.1")) == 1
    # подписчик появился ПОСЛЕ — replay/cursor отсутствует (документировано
    # в docs/Архитектура-2.0 §3.11 и шапке core/events.py)
    seen = []
    subscribe(seen.append)
    assert seen == []
    # доездывает только новое событие, история молчит
    emit("device.offline", con=events_con, ip="10.0.0.1")
    events_con.commit()
    assert [p["name"] for p in seen] == ["device.offline"]
    # обе записи в истории (dup/replay не нужен — пишем ровно один раз)
    assert len(list_events(events_con, ip="10.0.0.1")) == 2


def test_notify_all_preserves_batch_order(events_con):
    """notify_all доставляет батч строго в порядке списка (§16)."""
    seen = []
    subscribe(lambda p: seen.append(p["ip"]))
    out = []
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        add_event(events_con, ip, event="NEW", out=out)
    events_con.commit()
    assert seen == []  # молчим до fan-out (notify — после commit)
    notify_all(out)
    assert seen == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert len(list_events(events_con)) == 3



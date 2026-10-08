# -*- coding: utf-8 -*-
"""Unit: motion — детекция движения, cooldown, очередь уведомлений,
hook, роуты API (фаза «События и оповещения»)."""
import os
import time

import pytest

import app
import modules.auth as auth
import modules.motion_engine as engine
import modules.motion_notify as notify


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = True


@pytest.fixture()
def motion(tmp_path, monkeypatch, devices_db):
    """motion в tmp: фото-каталог, настройки, чистое runtime-состояние."""
    photo_dir = tmp_path / "motion"
    monkeypatch.setattr(engine, "MOTION_DIR", str(photo_dir))
    monkeypatch.setattr(engine, "TMP_DIR", str(photo_dir / ".tmp"))
    monkeypatch.setattr(app.core_config, "SETTINGS_PATH",
                        str(tmp_path / "settings.json"))
    app.core_config.clear_cache()
    engine._last_trigger.clear()
    engine._state.clear()
    engine._silence_until = 0.0
    notify._net.update({"ok": None, "down_since": None,
                        "last_down": 0.0, "last_up": 0.0})

    def set_settings(data):
        import json
        with open(str(tmp_path / "settings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"motion": data}, f, ensure_ascii=False)
        app.core_config.clear_cache()

    app.app.config["TESTING"] = True
    yield set_settings
    app.core_config.clear_cache()


def _jpg(tmp_path, name="f.jpg", color=(200, 30, 30)):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (640, 360), color).save(str(p), "JPEG")
    return str(p)


def _mcfg(**over):
    c = dict(engine.DEFAULTS)
    c.update({"enabled": True, "cooldown_sec": 0, "channels": {
        "telegram": {"enabled": True, "bot_token": "t", "chat_id": "1"},
        "email": {"enabled": False},
    }})
    c.update(over)
    return c


# ==================== Детектор ====================

def test_diff_ratio_identical_vs_changed(tmp_path):
    pytest.importorskip("PIL")
    a = _jpg(tmp_path, "a.jpg")
    b = _jpg(tmp_path, "b.jpg")
    assert engine.diff_ratio(a, b) < 0.01
    c = _jpg(tmp_path, "c.jpg", color=(30, 30, 200))
    assert engine.diff_ratio(a, c) > 0.5


# ==================== События / cooldown ====================

def test_trigger_creates_event_and_queue_row(motion, tmp_path):
    photo = _jpg(tmp_path, "shot.jpg")
    eid = engine.trigger_event("7", 0.5, "snapshot", photo=photo,
                               cfg_override=_mcfg())
    assert eid
    from modules.devices_routes import get_db
    con = get_db()
    try:
        ev = con.execute("SELECT camera_name, detector FROM motion_events "
                         "WHERE id=?", (eid,)).fetchone()
        assert ev == ("7", "snapshot")
        hist = con.execute(
            "SELECT COUNT(*) FROM events WHERE event='MOTION' "
            "AND source='motion'").fetchone()[0]
        assert hist == 1
        q = con.execute(
            "SELECT status, channel FROM motion_queue WHERE event_id=?",
            (eid,)).fetchall()
        assert q and q[0] == ("pending", "telegram")
    finally:
        con.close()
    photos = [os.path.join(dp, f)
              for dp, _, fs in os.walk(engine.MOTION_DIR)
              for f in fs if f.endswith(".jpg") and ".tmp" not in dp]
    assert photos and all(os.path.isfile(p) for p in photos)


def test_cooldown_blocks_duplicates(motion, tmp_path):
    p = _jpg(tmp_path)
    c = _mcfg(cooldown_sec=3600)
    first = engine.trigger_event("1", 0.4, "snapshot", photo=p,
                                 cfg_override=c)
    second = engine.trigger_event("1", 0.9, "snapshot", photo=p,
                                  cfg_override=c)
    assert first and second is None


def test_disabled_or_off_camera_ignored(motion, tmp_path):
    p = _jpg(tmp_path)
    assert engine.trigger_event("1", 0.5, "snapshot", photo=p,
                                cfg_override=_mcfg(enabled=False)) is None
    c = _mcfg(cameras={"1": {"mode": "off"}})
    assert engine.trigger_event("1", 0.5, "snapshot", photo=p,
                                cfg_override=c) is None


# ==================== Очередь уведомлений ====================

def test_backoff_curve():
    assert notify.backoff(1) == 30
    assert notify.backoff(2) == 60
    assert notify.backoff(20) == 3600


def test_queue_deferred_offline_then_sent_after_recovery(
        motion, tmp_path, monkeypatch):
    """Ключевой сценарий: нет интернета → лежит → связь есть → ушло."""
    p = _jpg(tmp_path)
    c = _mcfg()
    eid = engine.trigger_event("2", 0.5, "snapshot", photo=p,
                               cfg_override=c)
    assert eid

    stats = notify.process_queue(cfg_override=c, inet_ok=False)
    assert stats["sent"] == 0
    assert stats["pending"] == 1

    sent = []
    monkeypatch.setattr(
        notify, "_send",
        lambda channel, ch, photo, caption, cfg: sent.append(channel))
    stats = notify.process_queue(cfg_override=c, inet_ok=True)
    assert stats["sent"] == 1
    assert sent == ["telegram"]

    from modules.devices_routes import get_db
    con = get_db()
    try:
        row = con.execute("SELECT status FROM motion_queue WHERE event_id=?",
                          (eid,)).fetchone()
        assert row[0] == "sent"
    finally:
        con.close()


def test_queue_error_backoff_and_ttl_dead(motion, tmp_path, monkeypatch):
    p = _jpg(tmp_path)
    c = _mcfg()
    eid = engine.trigger_event("3", 0.5, "snapshot", photo=p,
                               cfg_override=c)

    def _boom(*a, **k):
        raise RuntimeError("нет связи")

    monkeypatch.setattr(notify, "_send", _boom)
    notify.process_queue(cfg_override=c, inet_ok=True)
    from modules.devices_routes import get_db
    con = get_db()
    try:
        row = con.execute(
            "SELECT attempts, next_epoch, status, last_error "
            "FROM motion_queue WHERE event_id=?", (eid,)).fetchone()
        assert row[0] == 1
        assert row[1] > time.time()
        assert row[2] == "pending"
        assert "нет связи" in row[3]
        # TTL: старая строка уходит в dead
        con.execute("UPDATE motion_queue SET created_epoch=? WHERE event_id=?",
                    (int(time.time()) - 100 * 86400, eid))
        con.commit()
    finally:
        con.close()
    stats = notify.process_queue(cfg_override=c, inet_ok=False)
    assert stats["dead"] == 1
    con = get_db()
    try:
        assert con.execute(
            "SELECT status FROM motion_queue WHERE event_id=?",
            (eid,)).fetchone()[0] == "dead"
    finally:
        con.close()


def test_net_watch_creates_events(motion):
    from modules.devices_routes import get_db
    notify._watch_internet(True, {})
    notify._watch_internet(False, {})
    notify._net["down_since"] = time.time() - 120
    notify._watch_internet(False, {})
    notify._watch_internet(True, {})
    con = get_db()
    try:
        rows = [r[0] for r in con.execute(
            "SELECT event FROM events WHERE source='motion' "
            "AND event LIKE 'MOTION_NET%'")]
    finally:
        con.close()
    assert "MOTION_NET_DOWN" in rows
    assert "MOTION_NET_UP" in rows


# ==================== Каналы ====================

def test_telegram_send_ok_and_error(monkeypatch):
    calls = {}

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def _post(url, data=None, files=None, timeout=None):
        calls["url"] = url
        return _Resp({"ok": True})

    import requests
    monkeypatch.setattr(requests, "post", _post)
    notify.send_telegram({"bot_token": "tok", "chat_id": "5"},
                         None, "привет")
    assert "sendPhoto" not in calls["url"]  # без фото → sendMessage
    assert "sendMessage" in calls["url"]

    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _Resp({"ok": False, "description": "chat not found"}))
    with pytest.raises(RuntimeError, match="chat not found"):
        notify.send_telegram({"bot_token": "tok", "chat_id": "5"},
                             None, "x")


def test_email_send_smtp(monkeypatch):
    sent = {}

    class _SMTP:
        def __init__(self, host, port, timeout=None):
            sent["hostport"] = (host, port)

        def ehlo(self):
            pass

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def sendmail(self, frm, to, msg):
            sent["to"] = to
            sent["msg"] = msg

        def quit(self):
            sent["quit"] = True

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    notify.send_email(
        {"host": "smtp.example", "port": 587, "starttls": True,
         "user": "u", "password": "p", "to_addr": "me@example.com"},
        None, "тест")
    assert sent["hostport"] == ("smtp.example", 587)
    assert sent.get("tls") and sent.get("quit")
    assert sent["to"] == ["me@example.com"]


def test_shrink_compresses_big_photo(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    p = tmp_path / "big.jpg"
    Image.effect_noise((1920, 1080), 64).convert("RGB").save(
        str(p), "JPEG", quality=95)
    out = notify._shrink(str(p), 100)
    assert out and os.path.getsize(out) <= 100 * 1024
    assert os.path.getsize(str(p)) > 100 * 1024


# ==================== Роуты ====================

def test_status_requires_login(monkeypatch):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(auth, "load_users",
                        lambda: {"admin": {"enabled": True, "role": "admin"}})
    c = app.app.test_client()
    r = c.get("/api/motion/status")
    assert r.status_code in (302, 401)
    app.app.config["WTF_CSRF_ENABLED"] = True


def test_status_and_events_for_admin(client, motion):
    motion({"enabled": True, "channels": {
        "telegram": {"enabled": True, "bot_token": "t", "chat_id": "1"}}})
    r = client.get("/api/motion/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["enabled"] is True
    assert data["channels"]["telegram"] is True
    assert "queue" in data and "cameras" in data

    engine.trigger_event("9", 0.7, "hook", photo=None,
                         cfg_override=_mcfg())
    r = client.get("/api/motion/events")
    assert r.status_code == 200
    assert any(e["detector"] == "hook" for e in r.get_json())


def test_photo_route_serves_and_protects(client, motion, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    os.makedirs(engine.MOTION_DIR, exist_ok=True)
    p = os.path.join(engine.MOTION_DIR, "real.jpg")
    Image.new("RGB", (64, 48), (1, 2, 3)).save(p, "JPEG")
    from modules.devices_routes import get_db
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO motion_events (ts, epoch, camera_id, camera_name, "
            "score, detector, photo) VALUES ('01.01.2026 00:00:00', 1, 1, "
            "'cam', 0.5, 'snapshot', ?)", (p,))
        eid = cur.lastrowid
        con.execute(
            "INSERT INTO motion_events (ts, epoch, camera_id, camera_name, "
            "score, detector, photo) VALUES ('01.01.2026 00:00:01', 2, 1, "
            "'cam', 0.5, 'snapshot', '/etc/passwd')")
        bad = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit()
    finally:
        con.close()
    r = client.get(f"/api/motion/photo/{eid}")
    assert r.status_code == 200
    r = client.get(f"/api/motion/photo/{bad}")
    assert r.status_code == 404


def test_hook_endpoint(motion, client, tmp_path):
    motion({"enabled": True, "hook_token": "secr3t",
            "cooldown_sec": 0,
            "channels": {"telegram": {"enabled": False}}})
    r = client.post("/api/motion/hook/wrong?cam=11")
    assert r.status_code == 404
    r = client.post("/api/motion/hook/secr3t?cam=12")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True and data["event"]
    r = client.get("/api/motion/hook/secr3t")
    assert r.status_code == 400  # cam не указан
    from modules.devices_routes import get_db
    con = get_db()
    try:
        n = con.execute("SELECT COUNT(*) FROM motion_events "
                        "WHERE camera_id=12").fetchone()[0]
    finally:
        con.close()
    assert n == 1


def test_silence_and_notify_test_rbac(client, motion):
    motion({"enabled": True, "channels": {
        "telegram": {"enabled": False}, "email": {"enabled": False}}})
    r = client.post("/api/motion/silence", json={"minutes": 15})
    assert r.status_code == 200
    assert r.get_json()["silence_until"] > time.time()
    r = client.post("/api/motion/notify/test", json={"channel": "telegram"})
    assert r.status_code == 400  # канал выключен
    r = client.post("/api/motion/notify/test", json={"channel": "sms"})
    assert r.status_code == 400


def test_queue_event_respects_enabled_channels(motion):
    from modules.devices_routes import get_db
    c = _mcfg(channels={"telegram": {"enabled": False},
                        "email": {"enabled": False}})
    assert notify.queue_event(1, None, "x", cfg_override=c) == 0
    c2 = _mcfg()
    assert notify.queue_event(1, None, "x", cfg_override=c2) == 1


def test_hook_exempt_from_csrf(motion):
    """POST на hook без CSRF-токена должен работать (камеры не шлют формы).

    Регрессия: при запуске как __main__ импорт csrf из app брал чужой
    экземпляр — exempt терялся и камеры получали 400."""
    motion({"enabled": True, "hook_token": "tok2", "cooldown_sec": 0,
            "channels": {"telegram": {"enabled": False}}})
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.app.test_client()
        r = c.post("/api/motion/hook/tok2?cam=77")
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["event"]
    finally:
        app.app.config["WTF_CSRF_ENABLED"] = old

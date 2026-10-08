# -*- coding: utf-8 -*-
"""Детекция движения (см. modules/motion/help.md).

Детекторы: snapshot (снимок + пиксельный diff), onvif (PullPoint, best-effort
с fallback на snapshot), hook (камера дёргает /api/motion/hook/<token>).
Дедуп: per-camera cooldown. Фото — /srv/media/motion/<дата>/<cam>_<epoch>.jpg,
события — таблица motion_events + общая История (source='motion').
Локальная тревога (alarm) не зависит от интернета.

Зависимости: Pillow (snapshot-diff), ffmpeg (кадр из RTSP).
"""
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime

log = logging.getLogger("lan-discovery")

CAMERAS_CONFIG = "/etc/lan-discovery/cameras.json"
MOTION_DIR = "/srv/media/motion"
TMP_DIR = os.path.join(MOTION_DIR, ".tmp")

DEFAULTS = {
    "enabled": False,
    "interval_sec": 5,
    "cooldown_sec": 180,
    "threshold": 0.10,
    "probe_min": 5,
    "retention_days": 30,
    "max_photo_kb": 300,
    "queue_ttl_days": 60,
    "hook_token": "",
    "alarm": {"enabled": False, "mode": "none", "cooldown_sec": 60},
    "channels": {
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "email": {"enabled": False, "host": "", "port": 587, "starttls": True,
                  "user": "", "password": "", "from_addr": "", "to_addr": ""},
    },
    "cameras": {},
}

_lock = threading.Lock()
_state = {}          # cam_id -> runtime (prev, ratio, up, fails, mode_fallback)
_last_trigger = {}   # cam_id -> epoch
_alarm_state = {"last": 0.0}
_silence_until = 0.0
_started = False


# ==================== Настройки ====================

def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def cfg():
    """Настройки motion из settings.json с глубоким merge дефолтов."""
    from app import load_settings
    raw = load_settings().get("motion")
    out = json.loads(json.dumps(DEFAULTS))
    if isinstance(raw, dict):
        _deep_merge(out, raw)
    return out


def load_cameras():
    """Камеры из общего cameras.json (формат модуля camera)."""
    try:
        with open(CAMERAS_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _cam_by_id(cam_id, cam=None):
    if cam is not None:
        return cam
    cid = str(cam_id)
    return next((x for x in load_cameras() if str(x.get("id")) == cid), None)


# ==================== Детектор snapshot ====================

def diff_ratio(path_a, path_b):
    """Доля изменившихся пикселей (L, 160x90, порог яркости 25)."""
    from PIL import Image, ImageChops
    size = (160, 90)
    a = Image.open(path_a).convert("L").resize(size)
    b = Image.open(path_b).convert("L").resize(size)
    hist = ImageChops.difference(a, b).histogram()
    changed = sum(hist[25:])
    return changed / float(size[0] * size[1])


def _basic_auth(ccfg):
    user = (ccfg.get("http_user") or "").strip()
    if not user:
        return None
    return (user, ccfg.get("http_pass") or "")


def grab_snapshot(cam, ccfg, dest):
    """Сохранить JPEG-кадр в dest (ffmpeg для RTSP, прямой GET для HTTP)."""
    url = (ccfg.get("snapshot_url") or (cam or {}).get("url") or "").strip()
    if not url:
        return False
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    except OSError:
        pass
    if url.lower().startswith("rtsp"):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-rtsp_transport", "tcp", "-i", url,
               "-frames:v", "1", "-q:v", "4", dest]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            return (r.returncode == 0 and os.path.isfile(dest)
                    and os.path.getsize(dest) > 0)
        except Exception:
            return False
    try:
        import requests
        r = requests.get(url, timeout=8, auth=_basic_auth(ccfg))
        if r.status_code != 200:
            return False
        data = r.content
        if len(data) < 4:
            return False
        ctype = (r.headers.get("Content-Type") or "").lower()
        if data[:2] != b"\xff\xd8" and "jpeg" not in ctype and "jpg" not in ctype:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _store_photo(src, cam_id, epoch):
    day = datetime.now().strftime("%Y-%m-%d")
    dst_dir = os.path.join(MOTION_DIR, day)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, f"{cam_id}_{epoch}.jpg")
        shutil.copyfile(src, dst)
        return dst
    except Exception as e:
        log.error(f"MOTION PHOTO STORE ERROR: {e}")
        return None


def _cam_ip(cam):
    url = (cam or {}).get("url") or (cam or {}).get("snapshot_url") or ""
    try:
        return urllib.parse.urlparse(url).hostname or None
    except Exception:
        return None


# ==================== Событие ====================

def trigger_event(cam_id, score, detector, photo=None, cam=None,
                  cfg_override=None):
    """Создать motion-событие (cooldown-gated); id события или None."""
    from modules import motion_notify
    c = cfg_override if cfg_override is not None else cfg()
    if not c.get("enabled"):
        return None
    cid = str(cam_id)
    cc = (c.get("cameras") or {}).get(cid, {})
    if cc.get("mode", "snapshot") == "off":
        return None
    now = time.time()
    cooldown = int(cc.get("cooldown_sec", c.get("cooldown_sec", 180)) or 0)
    with _lock:
        if now - _last_trigger.get(cid, 0) < cooldown:
            return None
        _last_trigger[cid] = now

    epoch = int(now)
    cam = _cam_by_id(cid, cam)
    if photo and os.path.isfile(photo):
        stored = _store_photo(photo, cid, epoch)
    else:
        tmp = os.path.join(TMP_DIR, f"cap_{cid}.jpg")
        if grab_snapshot(cam or {}, cc, tmp):
            stored = _store_photo(tmp, cid, epoch)
        else:
            stored = None

    name = (cam or {}).get("name") or cid
    ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    from modules.devices_routes import get_db
    from core.events import add_event
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO motion_events "
            "(ts, epoch, camera_id, camera_name, score, detector, photo) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, epoch, int(cid) if cid.isdigit() else 0, name,
             round(float(score or 0), 4), detector, stored),
        )
        eid = cur.lastrowid
        add_event(con, ip=_cam_ip(cam), hostname=name, event="MOTION",
                  source="motion", severity="warning", timestamp=ts,
                  metadata={"camera": name, "camera_id": cid,
                            "score": round(float(score or 0), 3),
                            "detector": detector, "event_id": eid})
        con.commit()
    except Exception as e:
        try:
            con.rollback()
        except Exception:
            pass
        log.error(f"MOTION EVENT INSERT ERROR: {e}")
        return None
    finally:
        con.close()

    try:
        motion_notify.queue_event(eid, stored,
                                  f"Движение: {name} {ts}", cfg_override=c)
    except Exception as e:
        log.error(f"MOTION ENQUEUE ERROR: {e}")

    alarm_trigger(f"Движение: {name}", c)
    _emit({"type": "motion_event", "event_id": eid, "camera": name,
           "camera_id": cid, "ts": ts, "detector": detector,
           "score": round(float(score or 0), 3)})
    log.info(f"MOTION: cam={name} detector={detector} "
             f"score={score} photo={stored}")
    return eid


# ==================== Тревога/пауза ====================

def silence(minutes):
    """Пауза локальной тревоги на N минут (эпоха)."""
    global _silence_until
    minutes = max(0, int(minutes or 0))
    with _lock:
        _silence_until = time.time() + minutes * 60
        return _silence_until


def alarm_trigger(text, c=None):
    """Локальная тревога (не зависит от интернета): beep/none + SocketIO."""
    global _silence_until
    c = c if c is not None else cfg()
    now = time.time()
    with _lock:
        if now < _silence_until:
            return
    a = c.get("alarm") or {}
    if not a.get("enabled"):
        return
    if now - _alarm_state["last"] < int(a.get("cooldown_sec", 60) or 0):
        return
    _alarm_state["last"] = now
    mode = a.get("mode", "none")
    if mode == "beep":
        fpath = os.path.join(MOTION_DIR, "alarm.wav")
        if os.path.isfile(fpath):
            try:
                subprocess.Popen(["aplay", "-q", fpath])
            except Exception:
                pass
    log.warning(f"MOTION ALARM: {text}")
    _emit({"type": "motion_alarm", "text": text, "ts": now})


def _emit(payload):
    try:
        from app import socketio
        socketio.emit("motion_event", payload)
    except Exception:
        pass


# ==================== Детектор onvif (best-effort) ====================

_onvif_running = set()


def _onvif_watch(cid, cam, cc):
    """Подписка на ONVIF MotionAlarm (PullPoint). При ошибке — fallback."""
    try:
        from onvif import ONVIFCamera  # onvif-zeep-async
        host = _cam_ip(cam)
        if not host:
            raise RuntimeError("нет host в URL камеры")
        port = int((cc.get("onvif_port") or 80) or 80)
        user = cc.get("onvif_user") or ""
        password = cc.get("onvif_pass") or ""
        camera = ONVIFCamera(host, port, user, password)
        # PullPoint-подписка; API зависит от версии библиотеки — при любой
        # ошибке детектор переходит на snapshot (см. _poll_all).
        media = camera.create_media_service()
        cap = camera.get_capabilities()
        event = camera.create_event_service()
        topic = (b'//www.onvif.org/ver20/Rule/VideoSource/MotionAlarm'
                 if hasattr(cap, "Events") else
                 b'//www.onvif.org/ver10/Rule/VideoSource/MotionAlarm')
        pull = event.CreatePullPointSubscription(
            {"InitialTerminationTime": "PT1M"})
        sub = pull[0] if isinstance(pull, (list, tuple)) else pull
        while True:
            resp = event.PullMessages(
                {"SubscriptionReference": sub.SubscriptionReference,
                 "Timeout": "PT30S", "MessageLimit": 10})
            msgs = resp[0] if isinstance(resp, (list, tuple)) else resp
            for msg in getattr(msgs, "NotificationMessage", []) or []:
                data = getattr(msg, "Data", None)
                attr = getattr(data, "SimpleItem", None) or []
                for item in (attr if isinstance(attr, list) else [attr]):
                    if getattr(item, "Name", "") == "IsMotion":
                        if str(getattr(item, "Value", "")).lower() == "true":
                            trigger_event(cid, 1.0, "onvif", cam=cam)
            time.sleep(1)
    except Exception as e:
        log.warning(f"MOTION ONVIF ({cid}): fallback на snapshot — {e}")
        with _lock:
            st = _state.setdefault(cid, {})
            st["mode_fallback"] = True
        _onvif_running.discard(cid)


# ==================== Пинг камер ====================

def _cam_hostport(cam, cc=None):
    url = (cc or {}).get("snapshot_url") or (cam or {}).get("url") or ""
    try:
        p = urllib.parse.urlparse(url)
        if not p.hostname:
            return None
        port = p.port or (443 if p.scheme == "https" else
                          80 if p.scheme in ("http", "https") else 554)
        return p.hostname, port
    except Exception:
        return None


def probe_camera(cam, cc=None, timeout=4):
    hp = _cam_hostport(cam, cc)
    if not hp:
        return False
    try:
        with socket.create_connection(hp, timeout=timeout):
            return True
    except Exception:
        return False


def _probe_all(c, now):
    from modules.devices_routes import get_db
    from core.events import add_event
    for cam in load_cameras():
        cid = str(cam.get("id"))
        cc = (c.get("cameras") or {}).get(cid, {})
        if cc.get("mode", "snapshot") == "off":
            continue
        st = _state.setdefault(cid, {})
        up = probe_camera(cam, cc)
        prev = st.get("up")
        st["up"] = up
        if up and prev is False and now - st.get("evt_ts", 0) > 3600:
            st["evt_ts"] = now
            _cam_event(cam, "MOTION_CAM_UP", "info",
                       "камера снова на связи", now)
        elif not up and prev in (True, None):
            st["fails"] = st.get("fails", 0) + 1
            if st["fails"] >= 2 and now - st.get("evt_ts", 0) > 3600:
                st["evt_ts"] = now
                _cam_event(cam, "MOTION_CAM_DOWN", "warning",
                           "нет связи с камерой", now)
        elif up:
            st["fails"] = 0


def _cam_event(cam, event, severity, note, now):
    from modules.devices_routes import get_db
    from core.events import add_event
    con = get_db()
    try:
        add_event(con, ip=_cam_ip(cam), hostname=(cam or {}).get("name"),
                  event=event, source="motion", severity=severity,
                  metadata={"note": note})
        con.commit()
        log.warning(f"MOTION {event}: {(cam or {}).get('name')} — {note}")
    except Exception as e:
        log.error(f"MOTION CAM EVENT ERROR: {e}")
    finally:
        con.close()


# ==================== Цикл engine ====================

def _poll_snapshot(cam, cc, c, st):
    cid = str(cam.get("id"))
    tmp = os.path.join(TMP_DIR, f"{cid}.jpg")
    prev = os.path.join(TMP_DIR, f"prev_{cid}.jpg")
    if not grab_snapshot(cam, cc, tmp):
        st["fails"] = st.get("fails", 0) + 1
        return
    st["fails"] = 0
    ratio = None
    if os.path.isfile(prev):
        try:
            ratio = diff_ratio(prev, tmp)
        except Exception as e:
            log.debug(f"MOTION DIFF ERROR ({cid}): {e}")
    try:
        shutil.copyfile(tmp, prev)
    except OSError:
        pass
    if ratio is None:
        return
    st["ratio"] = round(ratio, 4)
    th = float(cc.get("threshold", c.get("threshold", 0.10)) or 0.10)
    if ratio >= th:
        trigger_event(cid, ratio, "snapshot", photo=tmp, cam=cam,
                      cfg_override=c)


def _poll_all(c, now):
    cams = load_cameras()
    for cam in cams:
        cid = str(cam.get("id"))
        cc = (c.get("cameras") or {}).get(cid, {})
        st = _state.setdefault(cid, {})
        mode = "snapshot" if st.get("mode_fallback") else cc.get(
            "mode", "snapshot")
        if mode in ("off", "hook"):
            continue
        if mode == "onvif":
            if cid not in _onvif_running:
                _onvif_running.add(cid)
                threading.Thread(target=_onvif_watch, args=(cid, cam, cc),
                                 daemon=True, name=f"motion-onvif-{cid}"
                                 ).start()
            continue
        interval = int(cc.get("poll_sec", c.get("interval_sec", 5)) or 5)
        if now - st.get("next_poll", 0) < interval:
            continue
        st["next_poll"] = now
        try:
            _poll_snapshot(cam, cc, c, st)
        except Exception as e:
            log.error(f"MOTION POLL ERROR ({cid}): {e}")


def cleanup(days):
    """Удалить события/фото старше days дней."""
    if days <= 0:
        return 0
    from modules.devices_routes import get_db
    cutoff = int(time.time()) - days * 86400
    con = get_db()
    removed = 0
    try:
        rows = con.execute(
            "SELECT id, photo FROM motion_events WHERE epoch < ?",
            (cutoff,)).fetchall()
        ids = [r[0] for r in rows]
        for eid, photo in rows:
            if photo and os.path.isfile(photo):
                try:
                    os.remove(photo)
                except OSError:
                    pass
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            marks = ",".join("?" * len(batch))
            con.execute(f"DELETE FROM motion_events WHERE id IN ({marks})",
                        batch)
            con.execute(f"DELETE FROM motion_queue WHERE event_id IN ({marks})",
                        batch)
        con.commit()
        removed = len(ids)
        if removed:
            log.info(f"MOTION RETENTION: удалено {removed} событий "
                     f"старше {days} дней")
    finally:
        con.close()
    return removed


def _engine_loop():
    last_probe = 0.0
    last_retention = 0.0
    while True:
        time.sleep(1)
        try:
            import app as _app
            if _app.app.config.get("TESTING"):
                continue
            c = cfg()
            if not c.get("enabled"):
                continue
            now = time.time()
            _poll_all(c, now)
            probe_min = int(c.get("probe_min", 5) or 0)
            if probe_min > 0 and now - last_probe >= probe_min * 60:
                last_probe = now
                _probe_all(c, now)
            ret_days = int(c.get("retention_days", 30) or 0)
            if ret_days > 0 and now - last_retention >= 86400:
                last_retention = now
                cleanup(ret_days)
        except Exception as e:
            log.error(f"MOTION ENGINE ERROR: {e}")


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_engine_loop, daemon=True,
                     name="motion-engine").start()
    from modules import motion_notify
    motion_notify.start()


# ==================== Статус ====================

def get_runtime():
    """Runtime-состояние камер для статуса."""
    out = {}
    c = cfg()
    for cam in load_cameras():
        cid = str(cam.get("id"))
        cc = (c.get("cameras") or {}).get(cid, {})
        st = _state.get(cid, {})
        cooldown = int(cc.get("cooldown_sec", c.get("cooldown_sec", 180)) or 0)
        left = max(0, int(_last_trigger.get(cid, 0) + cooldown - time.time()))
        mode = "snapshot" if st.get("mode_fallback") else cc.get(
            "mode", "snapshot")
        out[cid] = {
            "name": cam.get("name"),
            "mode": mode,
            "mode_fallback": bool(st.get("mode_fallback")),
            "up": st.get("up"),
            "ratio": st.get("ratio"),
            "fails": st.get("fails", 0),
            "cooldown_left": left,
        }
    return out


def status_dict():
    c = cfg()
    from modules import motion_notify
    try:
        internet = None
        try:
            from app import check_internet_cached
            internet = check_internet_cached()
        except Exception:
            internet = None
        return {
            "enabled": bool(c.get("enabled")),
            "engine_running": bool(_started),
            "internet": internet,
            "silence_until": _silence_until,
            "channels": {k: bool((v or {}).get("enabled"))
                         for k, v in (c.get("channels") or {}).items()},
            "alarm": dict(c.get("alarm") or {}),
            "queue": motion_notify.queue_stats(c),
            "cameras": get_runtime(),
        }
    except Exception as e:
        return {"error": str(e)}

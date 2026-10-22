# -*- coding: utf-8 -*-
"""Дом/сигнализация — ядро модуля home.

Простая домашняя сигнализация поверх GPIO (libgpiod) с MQTT-мостом:

- зоны: GPIO-входы (контактные датчики门窗/reed) с trigger_level;
- сирена: GPIO-выход, включается при тревоге;
- охрана: armed/disarmed, цикл опроса зон (2 с) только при armed;
- MQTT: публикация состояния (retained) и событий на брокер
  (mosquitto-clients); без установленного клиента — MQTT disabled;
- датчики: CPU-температура (/sys/class/thermal) + значения GPIO-входов.

Всё состояние — в SQLite (home_zones, home_alarm, home_mqtt); GPIO —
только через gpioset/gpioget с валидацией chip/line (никаких
произвольных строк). Без GPIO-чипа зоны недоступны, охрана —
заглушка (эмитит события, сирены нет).
"""
import json
import os
import re
import threading
import time
from datetime import datetime

from core import config, events, process
from core.db import get_db

_CHIP_RE = re.compile(r"^gpiochip\d+$")
_LINE_MAX = 1024
POLL_INTERVAL = 2.0

# ----------------------------------------------------------------- DB

HOME_DDL = (
    """CREATE TABLE IF NOT EXISTS home_zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        chip TEXT NOT NULL,
        line INTEGER NOT NULL,
        trigger_level INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS home_alarm (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        armed INTEGER NOT NULL DEFAULT 0,
        triggered INTEGER NOT NULL DEFAULT 0,
        triggered_zone TEXT,
        siren_on INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS home_mqtt (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        host TEXT DEFAULT '',
        port INTEGER DEFAULT 1883,
        prefix TEXT DEFAULT 'lanpanel',
        enabled INTEGER NOT NULL DEFAULT 0
    )""",
)

_init_done = False
_init_lock = threading.Lock()


def init_home_db():
    global _init_done
    with _init_lock:
        if _init_done:
            return
        con = get_db()
        try:
            for stmt in HOME_DDL:
                con.execute(stmt)
            con.execute(
                "INSERT OR IGNORE INTO home_alarm (id, armed, triggered, "
                "triggered_zone, siren_on, updated_at) "
                "VALUES (1, 0, 0, NULL, 0, NULL)")
            con.execute(
                "INSERT OR IGNORE INTO home_mqtt (id, host, port, prefix, "
                "enabled) VALUES (1, '', 1883, 'lanpanel', 0)")
            con.commit()
        finally:
            con.close()
        _init_done = True


# --------------------------------------------------------------- GPIO

def gpio_available():
    return any(os.path.exists("/dev/%s" % c) for c in gpiochips())


def gpiochips():
    out = []
    try:
        for name in sorted(os.listdir("/dev")):
            if _CHIP_RE.match(name):
                out.append(name)
    except Exception:
        pass
    return out


def _valid_chip_line(chip, line):
    chip = os.path.basename(str(chip or ""))
    if not _CHIP_RE.match(chip):
        raise ValueError("неверный gpiochip: %s" % chip)
    line = int(line)
    if not 0 <= line < _LINE_MAX:
        raise ValueError("неверный line: %s" % line)
    return chip, line


def gpio_get(chip, line):
    """Прочитать уровень пина: 0/1 | None (ошибка)."""
    chip, line = _valid_chip_line(chip, line)
    r = process.out(["gpioget", chip, str(line)], timeout=3)
    r = r.strip()
    if r in ("0", "1"):
        return int(r)
    return None


def gpio_set(chip, line, value):
    """Установить уровень выхода (0/1). True при успехе."""
    chip, line = _valid_chip_line(chip, line)
    value = 1 if int(value) else 0
    try:
        process.run(["gpioset", chip, str(line), "value=%d" % value],
                    timeout=3)
        return True
    except Exception:
        return False


def cpu_temp():
    """CPU-температура °C (первый thermal_zone) | None."""
    for zone in sorted(os.listdir("/sys/class/thermal")
                       if os.path.isdir("/sys/class/thermal") else []):
        path = "/sys/class/thermal/%s/temp" % zone
        try:
            with open(path, "r") as f:
                milli = int(f.read().strip())
            if 0 < milli < 200000:
                return round(milli / 1000.0, 1)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- zones

def list_zones(con=None):
    own = con is None
    if own:
        con = get_db()
    try:
        rows = con.execute(
            "SELECT id, name, chip, line, trigger_level, enabled "
            "FROM home_zones ORDER BY id").fetchall()
        out = []
        for r in rows:
            z = {"id": r[0], "name": r[1], "chip": r[2], "line": r[3],
                 "trigger_level": r[4], "enabled": bool(r[5])}
            z["value"] = gpio_get(z["chip"], z["line"]) if (
                z["enabled"] and gpio_available()) else None
            z["open"] = (z["value"] == z["trigger_level"]
                         if z["value"] is not None else None)
            out.append(z)
        return out
    finally:
        if own:
            con.close()


def add_zone(name, chip, line, trigger_level=0):
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("укажите имя зоны")
    chip, line = _valid_chip_line(chip, line)
    trigger_level = 1 if int(trigger_level) else 0
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO home_zones (name, chip, line, trigger_level, "
            "enabled, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (name, chip, line, trigger_level,
             datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def delete_zone(zone_id):
    con = get_db()
    try:
        cur = con.execute("DELETE FROM home_zones WHERE id = ?", (int(zone_id),))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


# --------------------------------------------------------------- alarm

def _alarm_row(con):
    return con.execute(
        "SELECT armed, triggered, triggered_zone, siren_on, updated_at "
        "FROM home_alarm WHERE id = 1").fetchone()


def alarm_state():
    con = get_db()
    try:
        r = _alarm_row(con)
        return {"armed": bool(r[0]), "triggered": bool(r[1]),
                "triggered_zone": r[2], "siren_on": bool(r[3]),
                "updated_at": r[4]}
    finally:
        con.close()


def _save_alarm(con, armed=None, triggered=None, triggered_zone=None,
                siren_on=None):
    r = _alarm_row(con)
    vals = {
        "armed": r[0] if armed is None else int(bool(armed)),
        "triggered": r[1] if triggered is None else int(bool(triggered)),
        "triggered_zone": (r[2] if triggered_zone is None
                           else triggered_zone),
        "siren_on": r[3] if siren_on is None else int(bool(siren_on)),
    }
    con.execute(
        "UPDATE home_alarm SET armed=?, triggered=?, triggered_zone=?, "
        "siren_on=?, updated_at=? WHERE id = 1",
        (vals["armed"], vals["triggered"], vals["triggered_zone"],
         vals["siren_on"], datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
    con.commit()
    return vals


def _siren_zone(con):
    """Зона-сирена: GPIO-выход, хранится как зона '__siren__'."""
    r = con.execute(
        "SELECT chip, line FROM home_zones WHERE name = '__siren__' "
        "AND enabled = 1").fetchone()
    return (r[0], r[1]) if r else None


def set_siren_zone(chip, line):
    """Настроить GPIO-выход сирены; chip=None — убрать сирену."""
    con = get_db()
    try:
        con.execute("DELETE FROM home_zones WHERE name = '__siren__'")
        if chip:
            chip, line = _valid_chip_line(chip, line)
            con.execute(
                "INSERT INTO home_zones (name, chip, line, trigger_level, "
                "enabled, created_at) VALUES ('__siren__', ?, ?, 0, 1, ?)",
                (chip, line,
                 datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
        con.commit()
    finally:
        con.close()


def set_siren(value):
    """Вкл/выкл сирены вручную (тест)."""
    con = get_db()
    try:
        sz = _siren_zone(con)
        if not sz:
            raise RuntimeError("сирена не настроена (зона __siren__)")
        ok = gpio_set(sz[0], sz[1], value)
        _save_alarm(con, siren_on=bool(value) and ok)
        return ok
    finally:
        con.close()


def arm():
    con = get_db()
    try:
        _save_alarm(con, armed=True, triggered=False, triggered_zone=None,
                    siren_on=False)
        st = alarm_state()
    finally:
        con.close()
    _emit("home.alarm_armed", "охрана включена")
    mqtt_publish_state()
    _start_loop()
    return st


def disarm():
    con = get_db()
    try:
        sz = _siren_zone(con)
        if sz:
            gpio_set(sz[0], sz[1], 0)
        _save_alarm(con, armed=False, triggered=False, triggered_zone=None,
                    siren_on=False)
    finally:
        con.close()
    _stop_loop()
    _emit("home.alarm_disarmed", "охрана снята")
    mqtt_publish_state()
    return alarm_state()


def _emit(name, text, zone=None):
    meta = {"text": text}
    if zone:
        meta["zone"] = zone
    try:
        events.emit(name, ip="127.0.0.1", source="home", metadata=meta)
    except Exception:
        pass
    mqtt_publish_json({
        "event": name, "text": text, "zone": zone,
        "ts": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }, suffix="event")


# ------------------------------------------------------------- loop

_loop = {"thread": None, "stop": None, "lock": threading.Lock()}


def _loop_running():
    t = _loop["thread"]
    return t is not None and t.is_alive()


def _start_loop():
    with _loop["lock"]:
        if _loop_running():
            return
        _loop["stop"] = threading.Event()
        t = threading.Thread(target=_loop_fn, args=(_loop["stop"],),
                             daemon=True, name="home-alarm")
        _loop["thread"] = t
        t.start()


def _stop_loop():
    with _loop["lock"]:
        if _loop["stop"]:
            _loop["stop"].set()


def _loop_fn(stop):
    while not stop.is_set():
        try:
            _poll_once()
        except Exception:
            pass
        stop.wait(POLL_INTERVAL)


def _poll_once():
    con = get_db()
    try:
        r = _alarm_row(con)
        armed, triggered = bool(r[0]), bool(r[1])
        if not armed:
            return
        zones = con.execute(
            "SELECT id, name, chip, line, trigger_level FROM home_zones "
            "WHERE enabled = 1 AND name != '__siren__'").fetchall()
        opened = None
        for z in zones:
            val = gpio_get(z[2], z[3])
            if val is not None and val == z[4]:
                opened = z[1]
                break
        if opened and not triggered:
            _save_alarm(con, triggered=True, triggered_zone=opened)
            sz = _siren_zone(con)
            siren = False
            if sz:
                siren = gpio_set(sz[0], sz[1], 1)
            _save_alarm(con, siren_on=siren)
            _emit("home.alarm_triggered", "тревога: зона открыта",
                  zone=opened)
            mqtt_publish_state()
        elif not opened and triggered:
            # зона закрыта — тревога снимается только вручную (disarm),
            # но triggered_zone обновляем, чтобы UI показывал актуальную
            pass
    finally:
        con.close()


# --------------------------------------------------------------- MQTT

MQTT_ALLOWED_TOOLS = {"mosquitto-clients"}


def mqtt_config():
    con = get_db()
    try:
        r = con.execute(
            "SELECT host, port, prefix, enabled FROM home_mqtt "
            "WHERE id = 1").fetchone()
        return {"host": r[0] or "", "port": r[1] or 1883,
                "prefix": r[2] or "lanpanel", "enabled": bool(r[3])}
    finally:
        con.close()


def mqtt_save(host, port, prefix, enabled):
    host = (host or "").strip()[:255]
    prefix = re.sub(r"[^A-Za-z0-9_/.-]", "", (prefix or "lanpanel"))
    prefix = prefix.strip("/")[:80] or "lanpanel"
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("port 1..65535")
    con = get_db()
    try:
        con.execute(
            "UPDATE home_mqtt SET host=?, port=?, prefix=?, enabled=? "
            "WHERE id = 1", (host, port, prefix, int(bool(enabled))))
        con.commit()
    finally:
        con.close()
    mqtt_publish_state()


def mqtt_tool():
    return process.out(["which", "mosquitto_pub"], timeout=3).strip() or None


def _mqtt_send(topic, payload, retain=False):
    """mosquitto_pub: best-effort, таймаут 5 с. True при успехе."""
    cfg = mqtt_config()
    if not cfg["enabled"] or not cfg["host"] or not mqtt_tool():
        return False
    cmd = ["mosquitto_pub", "-h", cfg["host"], "-p", str(cfg["port"]),
           "-t", "%s/%s" % (cfg["prefix"], topic.lstrip("/")),
           "-m", payload]
    if retain:
        cmd.append("-r")
    try:
        r = process.run(cmd, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def mqtt_publish_json(obj, suffix="state", retain=True):
    return _mqtt_send(suffix, json.dumps(obj, ensure_ascii=False),
                      retain=retain)


def mqtt_publish_state():
    st = alarm_state()
    zones = list_zones()
    payload = {
        "armed": st["armed"], "triggered": st["triggered"],
        "triggered_zone": st["triggered_zone"], "siren": st["siren_on"],
        "zones": [{"name": z["name"], "open": z["open"]}
                  for z in zones if z["name"] != "__siren__"],
        "ts": st["updated_at"] or datetime.now().strftime(
            "%d.%m.%Y %H:%M:%S"),
    }
    return mqtt_publish_json(payload, suffix="state", retain=True)


def mqtt_test():
    cfg = mqtt_config()
    ok = _mqtt_send("test", json.dumps(
        {"msg": "LAN Discovery: тест MQTT", "ts": datetime.now().strftime(
            "%d.%m.%Y %H:%M:%S")}), retain=False)
    if not ok:
        if not mqtt_tool():
            raise RuntimeError(
                "MQTT не отправлен: установите mosquitto-clients "
                "(кнопка на странице)")
        raise RuntimeError(
            "клиент есть, но брокер недоступен: проверьте host:port (%s:%s)"
            % (cfg["host"], cfg["port"]))


# ------------------------------------------------------------ install

_install = {"running": False, "pkg": "", "log": []}
_install_lock = threading.Lock()


def install_status():
    return dict(_install)


def install(pkg):
    pkg = (pkg or "").strip()
    if pkg not in MQTT_ALLOWED_TOOLS:
        raise RuntimeError("пакет не в whitelist: %s" % pkg)
    with _install_lock:
        if _install["running"]:
            raise RuntimeError("установка уже выполняется")
        _install.update(running=True, pkg=pkg, log=[])
    threading.Thread(target=_install_worker, args=(pkg,), daemon=True).start()


def _install_worker(pkg):
    log = _install["log"]
    try:
        for cmd in (["apt-get", "update", "-qq"],
                    ["apt-get", "install", "-y", pkg]):
            log.append("$ " + " ".join(cmd))
            r = process.run(cmd, timeout=300)
            log.extend((r.stdout or "").splitlines()[-10:])
            if r.returncode != 0:
                raise RuntimeError("apt-get вернул %d" % r.returncode)
        log.append("OK: %s установлен" % pkg)
    except Exception as e:
        log.append("ERROR: %s" % e)
    finally:
        with _install_lock:
            _install["running"] = False


# --------------------------------------------------------------- status

def status():
    """Полный статус для страницы/API."""
    st = alarm_state()
    con = get_db()
    try:
        siren = _siren_zone(con)
    finally:
        con.close()
    st.update({
        "gpio": gpio_available(),
        "chips": gpiochips(),
        "temp": cpu_temp(),
        "loop": _loop_running(),
        "zones": list_zones(),
        "mqtt": mqtt_config(),
        "mqtt_tool": bool(mqtt_tool()),
        "siren_configured": siren is not None,
    })
    # авто-запуск цикла при arm после рестарта
    if st["armed"] and not st["loop"] and gpio_available():
        _start_loop()
        st["loop"] = _loop_running()
    return st

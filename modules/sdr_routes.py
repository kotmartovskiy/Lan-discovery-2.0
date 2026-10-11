# -*- coding: utf-8 -*-
"""HTTP/SocketIO модуль SDR-радио (виджет rtl_tcp).

Страница /apps/sdr: водопад/спектр + FM/AM в браузере. IQ-поток: один
серверный поток читает rtl_tcp (127.0.0.1:1234) и эмитит клиентам по
SocketIO namespace /sdr. rtl_tcp single-client — статус не открывает
пробных соединений при активном потоке.
"""
import logging
import socket
import struct
import subprocess
import threading
import time

from flask import jsonify, render_template, request

log = logging.getLogger("lan-discovery")

RTL_HOST = "127.0.0.1"
RTL_PORT = 1234
FREQ_MIN = 24_000_000
FREQ_MAX = 1_766_000_000
IQ_RATE_DEFAULT = 2_048_000

_state = {
    "freq": 100_000_000,
    "rate": IQ_RATE_DEFAULT,
    "running": False,
    "last_iq": 0.0,
    "error": None,
    "clients": 0,
}
_ctrl = {"sock": None}
_lock = threading.Lock()
_stop = threading.Event()
_thread = None


def _open_ctrl():
    s = socket.create_connection((RTL_HOST, RTL_PORT), timeout=5)
    s.recv(12)  # ответ rtl_tcp: magic + tuner + gain
    s.sendall(b"\x02" + struct.pack(">I", int(_state["rate"])))
    s.sendall(b"\x03\x01")  # AGC on
    s.sendall(b"\x01" + struct.pack(">I", int(_state["freq"])))
    return s


def _reader_loop():
    from app import socketio
    try:
        s = _open_ctrl()
    except OSError as e:
        _state["error"] = str(e)
        _state["running"] = False
        return
    with _lock:
        _ctrl["sock"] = s
    _state["error"] = None
    s.settimeout(2.0)
    try:
        while not _stop.is_set():
            try:
                chunk = s.recv(32768)
            except socket.timeout:
                continue
            except OSError as e:
                _state["error"] = str(e)
                break
            if not chunk:
                _state["error"] = "rtl_tcp closed stream"
                break
            _state["last_iq"] = time.time()
            socketio.emit("sdr_iq", chunk, namespace="/sdr")
    finally:
        with _lock:
            _ctrl["sock"] = None
        try:
            s.close()
        except OSError:
            pass
        _state["running"] = False


def _rtl_reachable(timeout=1.5):
    try:
        s = socket.create_connection((RTL_HOST, RTL_PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _ensure_stream():
    global _thread
    if _state["running"]:
        return True
    if not _rtl_reachable():
        return False
    _stop.clear()
    _state["running"] = True
    _thread = threading.Thread(target=_reader_loop, daemon=True, name="sdr-iq")
    _thread.start()
    return True


def _set_freq_now(freq):
    with _lock:
        s = _ctrl["sock"]
    if s is None:
        return False
    try:
        s.sendall(b"\x01" + struct.pack(">I", int(freq)))
        return True
    except OSError as e:
        _state["error"] = str(e)
        return False


def _stream_alive():
    return bool(_state["running"] and _state["last_iq"] and time.time() - _state["last_iq"] < 15)


def register_routes(app, ctx):
    login_required = ctx.login_required

    @app.route("/apps/sdr")
    @login_required
    def sdr_page():
        return render_template("sdr.html")

    @app.route("/api/sdr/status")
    @login_required
    def sdr_status():
        if _state["running"]:
            dongle = True
        else:
            dongle = _rtl_reachable()
            if dongle:
                _ensure_stream()
        return jsonify({
            "dongle": dongle,
            "streaming": _stream_alive(),
            "freq": int(_state["freq"]),
            "rate": int(_state["rate"]),
            "last_iq": _state["last_iq"],
            "error": _state["error"],
        })

    @app.route("/api/sdr/freq", methods=["POST"])
    @login_required
    def sdr_freq():
        data = request.get_json(silent=True) or {}
        try:
            freq = int(data.get("freq"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "freq must be int"}), 400
        if not (FREQ_MIN <= freq <= FREQ_MAX):
            return jsonify({"ok": False, "error": "freq out of range"}), 400
        _state["freq"] = freq
        applied = _set_freq_now(freq)
        return jsonify({"ok": True, "freq": freq, "applied": applied})

    @app.route("/api/sdr/rtl/start", methods=["POST"])
    @login_required
    def sdr_rtl_start():
        unit = "rtl_tcp.service"
        try:
            subprocess.run(
                ["systemctl", "start", unit],
                check=True, capture_output=True, timeout=20,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        time.sleep(0.8)
        return jsonify({"ok": True, "reachable": _rtl_reachable()})

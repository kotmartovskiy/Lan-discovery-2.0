import os
import sys
import json
import time
import re
import sqlite3
import logging
import subprocess
import shutil
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html import escape as _html_escape

sys.path.insert(0, "/opt/lan-discovery")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("lan-discovery")

DB = "/opt/lan-discovery/devices.db"
SETTINGS_PATH = "/etc/lan-discovery/settings.json"
USERS_PATH = "/etc/lan-discovery/users.json"
IPTV_CONFIG = "/etc/lan-discovery/iptv-playlists.json"
IPTV_DIR = "/srv/media/IPTV"
IPTV_UPDATE_STATUS = "/etc/lan-discovery/iptv-update-status.json"
GAMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games")
TRANSMISSION_CONF = "/etc/transmission-daemon/settings.json"
APP_VERSION = "2.0.0"
_SERVICE_START = time.time()

_settings_cache = {"data": None, "ts": 0}
_rate_limits = {}
_inet_cache = {"ok": None, "ts": 0}
_page_data_cache = {"data": None, "ts": 0}
_monitoring_cache = {"data": None, "ts": 0}


def load_settings():
    now = time.time()
    if _settings_cache["data"] is not None and now - _settings_cache["ts"] < 10:
        return _settings_cache["data"]
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _settings_cache["data"] = data
        _settings_cache["ts"] = now
        return data
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _settings_cache["data"] = data
        _settings_cache["ts"] = time.time()
    except Exception:
        pass


def _check_rate(action, cooldown=60):
    now = time.time()
    last = _rate_limits.get(action, 0)
    if now - last < cooldown:
        return False
    _rate_limits[action] = now
    return True


def _cfg(section, key, default=None):
    s = load_settings().get(section, {})
    return s.get(key, default)


def _scan_interval():
    return int(_cfg("network", "scan_interval", 30) or 30)


def _max_misses():
    return int(_cfg("network", "max_misses", 6) or 6)


def _cmd(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _format_dt(dt_str):
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return str(dt_str)


def _human_size(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def check_internet():
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "2", "1.1.1.1"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def check_internet_cached():
    now = time.time()
    if _inet_cache["ok"] is not None and now - _inet_cache["ts"] < 60:
        return _inet_cache["ok"]
    ok = check_internet()
    _inet_cache["ok"] = ok
    _inet_cache["ts"] = now
    return ok


def weather_current():
    con = None
    try:
        con = sqlite3.connect(DB, timeout=5)
        row = con.execute(
            "SELECT timestamp, temperature, apparent_temperature, humidity, precipitation, "
            "weather_code, wind_speed, wind_direction, pressure, cloud_cover "
            "FROM weather_observations ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {
            "timestamp": row[0], "temperature": row[1], "apparent_temperature": row[2],
            "humidity": row[3], "precipitation": row[4],
            "weather_code": row[5], "wind_speed": row[6], "wind_direction": row[7],
            "pressure": row[8], "cloud_cover": row[9] if len(row) > 9 else None
        }
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def currency_category_name(cat):
    return {"fiat": "Fiat валюты", "crypto": "Криптовалюта",
            "precious": "Драгоценные металлы", "industrial": "Промышленные металлы"}.get(cat, cat)


def recycling_category_name(cat):
    return {"paper": "Бумага / макулатура", "metal": "Металлы",
            "electronics": "Электроника"}.get(cat, cat)


def panel_name():
    """Имя панели для шапки/вкладки: settings.panel_name, иначе hostname.

    Позволяет различать несколько открытых панелей (X96 Max / Orange Pi).
    """
    name = str(load_settings().get("panel_name") or "").strip()
    if name:
        return name
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return ""


def page_data():
    now = time.time()
    if _page_data_cache["data"] is not None and now - _page_data_cache["ts"] < 10:
        return _page_data_cache["data"]
    data = {
        "internet": check_internet_cached(),
        "interval": _scan_interval(),
        "max_misses": _max_misses(),
        "weather": weather_current(),
        "panel_name": panel_name()
    }
    _page_data_cache["data"] = data
    _page_data_cache["ts"] = now
    return data


# ==================== Flask app ====================

from flask import Flask, request, redirect, url_for, render_template, session, jsonify, send_file
from flask_wtf.csrf import CSRFProtect

SECRET_KEY_PATH = "/etc/lan-discovery/secret.key"


def _load_or_create_secret_key():
    try:
        with open(SECRET_KEY_PATH, "rb") as f:
            key = f.read()
        if len(key) >= 32:
            return key.hex()
    except Exception:
        pass
    key = os.urandom(32)
    try:
        os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(key)
    except Exception:
        pass
    return key.hex()


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
csrf = CSRFProtect(app)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # панель работает по HTTP в LAN

from core.module_loader import MODULES_DIR, block_items
from jinja2 import ChoiceLoader, FileSystemLoader

app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader([MODULES_DIR])])

app.jinja_env.globals["currency_category_name"] = currency_category_name
app.jinja_env.globals["recycling_category_name"] = recycling_category_name
app.jinja_env.globals["_format_dt"] = _format_dt


@app.after_request
def _no_cache(resp):
    if request.path.startswith("/api/") or request.path.endswith(".json"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # PHASE 16 №60: vendor xterm/socket.io локально → внешние CDN в
    # script-src не нужны; inline покрывает шаблоны, ws:/cdn.jsdelivr.net
    # — socket.io-апгрейд и runtime-курсы валют, http(s) — iframe
    # Transmission на другом порту.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net; "
        "media-src 'self'; "
        "frame-src 'self' http: https:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'",
    )
    return resp


# §8.3 residual (находка №62, 01.10.2026): dev-сервер Werkzeug шлёт
# «Werkzeug/x.y Python/a.b» в заголовке Server (send_response →
# version_string) — подменяем на «lan-discovery», версии не раскрываем.
try:
    from werkzeug.serving import WSGIRequestHandler as _WZHandler
    _WZHandler.version_string = lambda self: "lan-discovery"
except Exception:  # pragma: no cover
    pass


# ==================== P1-11: барьер отсутствующих подсистем ====================

# Единый источник пробов — core/capabilities.py (STEP 7).
from core.capabilities import TOOL_PROBES as CAPABILITY_PROBES


def probe_capabilities():
    """Какие внешние бинарии доступны в PATH (однократно при старте)."""
    return {name: shutil.which(name) is not None
            for name in CAPABILITY_PROBES}


app.config["CAPABILITIES"] = probe_capabilities()
_missing_deps = [n for n, ok in app.config["CAPABILITIES"].items() if not ok]
if _missing_deps:
    app.logger.warning("MISSING DEPS: %s", ", ".join(_missing_deps))


@app.errorhandler(FileNotFoundError)
def _missing_dependency_error(e):
    """Системный барьер: отсутствующий бинарь/файл → 503, а не 500."""
    dep = getattr(e, "filename", None) or str(e)
    app.logger.warning("MISSING DEP %s %s -> %s",
                       request.method, request.path, dep)
    msg = "не установлена зависимость: %s" % dep
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": msg, "dependency": dep}), 503
    page = ("<!doctype html><html lang='ru'><meta charset='utf-8'>"
            "<title>503 — функция недоступна</title>"
            "<body style='font-family:sans-serif'>"
            "<h1>503 — функция недоступна</h1><p>%s</p>"
            "<p>Установите недостающий пакет и перезапустите панель "
            "(<code>systemctl restart lan-discovery</code>).</p>"
            "<p><a href='/'>На главную</a></p></body></html>") % _html_escape(dep)
    return page, 503, {"Content-Type": "text/html; charset=utf-8"}


@app.context_processor
def _inject_user():
    from types import SimpleNamespace
    from modules.auth import get_current_user, get_current_username
    u = get_current_user()
    if not u:
        u = SimpleNamespace(username="", role="guest", enabled=False, display_name="Гость")
    return {"current_user": u, "current_username": get_current_username(),
            "blocks_for": block_items}


# ==================== Auth ====================

from modules.auth import register_routes as register_auth_routes
register_auth_routes(app)

from modules.auth import login_required, admin_required, can_edit
import app as _app_module
_app_module.login_required = login_required
_app_module.admin_required = admin_required
_app_module.can_edit = can_edit

# ==================== Module system ====================

from modules.module_manager import register_routes as register_module_manager_routes
register_module_manager_routes(app, login_required, admin_required, page_data)

# ==================== Devices ====================

from modules.devices_routes import (
    register_routes as register_devices_routes,
    start_scan_thread,
    init_db_schema,
)
register_devices_routes(app)

# ==================== Weather ====================

from modules.weather_routes import register_routes as register_weather_routes
register_weather_routes(app)

# ==================== Currencies ====================

from modules.currencies import update_all as update_currencies
from modules.recycling import update_all as update_recycling


def update_currencies_background():
    try:
        update_currencies()
    except Exception as e:
        log.error("Currency update failed: %s", e)


def update_recycling_background():
    try:
        update_recycling()
    except Exception as e:
        log.error("Recycling update failed: %s", e)


# ==================== System ====================

from modules.system_routes import register_routes as register_system_routes, service_state
register_system_routes(app)

# ==================== Network ====================

from modules.network_routes import register_routes as register_network_routes
register_network_routes(app, login_required, admin_required, can_edit, _cmd, _cfg, page_data)

# ==================== Media ====================

from modules.media_routes import register_routes as register_media_routes
register_media_routes(app, login_required, admin_required, can_edit, _cmd, service_state, page_data)

# ==================== Monitor ====================

from modules.monitoring_routes import register_routes as register_monitoring_routes
register_monitoring_routes(app)

# ==================== Inventory ====================

from modules.inventory import init_inventory_db
from modules.inventory_routes import register_routes as register_inventory_routes
register_inventory_routes(app)

# ==================== Core routes ====================

from modules.core_routes import register_routes as register_core_routes
register_core_routes(app)


# ==================== Background tasks ====================

import schedule as _schedule


def _schedule_currencies():
    while True:
        _schedule.every(6).hours.do(update_currencies_background)
        _schedule.every(24).hours.do(update_recycling_background)
        while True:
            _schedule.run_pending()
            time.sleep(60)


# ==================== SocketIO ====================

from flask_socketio import SocketIO, emit

socketio = SocketIO(app, async_mode="threading")

from modules.core_routes import register_socketio_handlers
register_socketio_handlers(socketio)

# ==================== Main ====================

if __name__ == "__main__":
    init_db_schema()
    init_inventory_db()
    start_scan_thread()

    threading.Thread(target=update_currencies_background, daemon=True).start()
    threading.Thread(target=update_recycling_background, daemon=True).start()
    threading.Thread(target=_schedule_currencies, daemon=True).start()

    from core.events import retention_loop
    threading.Thread(target=retention_loop, daemon=True).start()

    socketio.run(app,
                 host=_cfg("web", "flask_host", "0.0.0.0"),
                 port=int(_cfg("web", "flask_port", 8080) or 8080),
                 allow_unsafe_werkzeug=True)

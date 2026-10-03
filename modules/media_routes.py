import json
import os
import subprocess
import time
import socket
import threading
import urllib.parse
from pathlib import Path
from datetime import datetime

from flask import request, redirect, url_for, jsonify, render_template

from core import storage

IPTV_CONFIG = "/etc/lan-discovery/iptv-playlists.json"
IPTV_DIR = storage.path("media", "IPTV")
IPTV_UPDATE_STATUS = "/etc/lan-discovery/iptv-update-status.json"
ALARM_FILE = "/etc/lan-discovery/alarms.json"
MEDIA_DIR = storage.path("media")
PLAYLISTS_DIR = storage.path("media", "playlists")
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff"}
CAMERAS_CONFIG = "/etc/lan-discovery/cameras.json"
TRANSMISSION_URL = "http://localhost:9091/transmission/rpc"
TRANSMISSION_USER = ""
TRANSMISSION_PASS = ""
TRANSMISSION_CONF = "/etc/transmission-daemon/settings.json"

_iptv_playlists_cache = {"data": None, "ts": 0}
_iptv_update_status_cache = {"data": None, "ts": 0}

_alarm_process = None
_radio_process = None
_radio_stations = []
_radio_index = 0

_player_process = None
_player_stations = []
_player_index = 0
_player_random = False
_player_order = []

_cameras = {}
_camera_processes = {}


def load_iptv_update_status():
    now = time.time()
    if _iptv_update_status_cache["data"] is not None and now - _iptv_update_status_cache["ts"] < 60:
        return _iptv_update_status_cache["data"]
    try:
        with open(IPTV_UPDATE_STATUS, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        _iptv_update_status_cache["data"] = data
        _iptv_update_status_cache["ts"] = now
        return data

    except Exception:
        return {}

def save_iptv_update_status(data):
    path = Path(IPTV_UPDATE_STATUS)

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4
        )
        f.write("\n")

    tmp.replace(path)

def load_iptv_playlists():
    now = time.time()
    if _iptv_playlists_cache["data"] is not None and now - _iptv_playlists_cache["ts"] < 60:
        return _iptv_playlists_cache["data"]
    try:
        with open(IPTV_CONFIG, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        _iptv_playlists_cache["data"] = data
        _iptv_playlists_cache["ts"] = now
        return data

    except Exception:
        return []

def save_iptv_playlists(playlists):
    path = Path(IPTV_CONFIG)

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(".json.tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            playlists,
            f,
            ensure_ascii=False,
            indent=4
        )
        f.write("\n")

    tmp.replace(path)
    _iptv_playlists_cache["data"] = None
    _iptv_playlists_cache["ts"] = 0

def iptv_update_status():
    try:
        state = service_state("update-iptv.service")

        if state == "active":
            return "running", None

        result = subprocess.run(
            [
                "journalctl",
                "-u", "update-iptv.service",
                "-n", "50",
                "--no-pager",
                "-o", "short"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        lines = result.stdout.splitlines()

        for line in reversed(lines):

            if "=== IPTV UPDATE OK:" in line:
                timestamp = line.split(
                    "=== IPTV UPDATE OK:",
                    1
                )[1].strip().rstrip("=").strip()

                return "ok", timestamp

            if "=== IPTV UPDATE WITH ERRORS:" in line:
                timestamp = line.split(
                    "=== IPTV UPDATE WITH ERRORS:",
                    1
                )[1].strip().rstrip("=").strip()

                return "error", timestamp

        # Fallback: read from status file
        try:
            with open(IPTV_UPDATE_STATUS, "r", encoding="utf-8") as f:
                status_data = json.load(f)
            latest = None
            for key, val in status_data.items():
                if isinstance(val, dict) and "status" in val:
                    if latest is None or val.get("time", "") > latest.get("time", ""):
                        latest = val
            if latest:
                return latest.get("status", "never"), latest.get("time")
        except Exception:
            pass

        return "never", None

    except Exception:
        return "never", None

def _load_alarms():
    try:
        with open(ALARM_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def _save_alarms(alarms):
    with open(ALARM_FILE, "w") as f:
        json.dump(alarms, f, indent=2, ensure_ascii=False)

def _parse_m3u(filepath):
    stations = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        name = ""
        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                parts = line.split(",", 1)
                if len(parts) > 1:
                    name = parts[1].strip()
            elif line and not line.startswith("#"):
                if line.startswith("http"):
                    stations.append({"name": name or line[:40], "url": line})
                    name = ""
    except:
        pass
    return stations

def _load_radio_stations():
    global _radio_stations
    stations = []
    try:
        for fn in os.listdir(IPTV_DIR):
            if fn.endswith((".m3u", ".m3u8")):
                stations.extend(_parse_m3u(os.path.join(IPTV_DIR, fn)))
    except:
        pass
    _radio_stations = stations
    return stations

def _radio_stop():
    global _radio_process
    try:
        if _radio_process and _radio_process.poll() is None:
            _radio_process.terminate()
        subprocess.Popen(["pkill", "-f", "mpv.*--no-video"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _radio_process = None
    except:
        pass

def _radio_play(url, volume=None):
    global _radio_process
    _radio_stop()
    try:
        cmd = ["mpv", "--no-video", "--really-quiet"]
        if volume is not None:
            cmd.extend(["--volume=" + str(int(volume))])
        cmd.append(url)
        _radio_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except:
        pass

def _player_stop():
    global _player_process
    try:
        if _player_process and _player_process.poll() is None:
            _player_process.terminate()
        subprocess.Popen(["pkill", "-f", "mpv.*--no-video"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _player_process = None
    except:
        pass

def _player_play(url, volume=None):
    global _player_process
    _player_stop()
    try:
        cmd = ["mpv", "--no-video", "--really-quiet"]
        if volume is not None:
            cmd.extend(["--volume=" + str(int(volume))])
        cmd.append(url)
        _player_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except:
        pass

def _scan_audio_files(path):
    files = []
    try:
        for entry in sorted(os.scandir(path)):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in AUDIO_EXTS:
                files.append({"name": os.path.splitext(entry.name)[0], "path": entry.path})
            elif entry.is_dir():
                files.extend(_scan_audio_files(entry.path))
    except:
        pass
    return files

def _load_cameras():
    try:
        with open(CAMERAS_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def _save_cameras(cameras):
    with open(CAMERAS_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cameras, f, ensure_ascii=False, indent=2)

def _start_camera_stream(cam):
    cam_id = cam["id"]
    _stop_camera_stream(cam_id)
    url = cam.get("url", "")
    if not url:
        return
    port = 9001 + (cam_id % 50)
    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", url,
        "-r", "3",
        "-f", "mjpeg",
        "-q:v", "5",
        "http://127.0.0.1:" + str(port) + "/stream"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _camera_processes[cam_id] = {"proc": proc, "port": port}
    except:
        pass

def _stop_camera_stream(cam_id):
    info = _camera_processes.pop(cam_id, None)
    if info and info["proc"].poll() is None:
        info["proc"].terminate()

def _transmission_rpc(method="GET", data=None, session_id=None):
    import requests
    headers = {"Content-Type": "application/rpc"}
    if TRANSMISSION_USER:
        import base64
        auth = base64.b64encode(("%s:%s" % (TRANSMISSION_USER, TRANSMISSION_PASS)).encode()).decode()
        headers["Authorization"] = "Basic %s" % auth
    if session_id:
        headers["X-Transmission-Session-Id"] = session_id
    try:
        if method == "GET":
            resp = requests.get(TRANSMISSION_URL, headers=headers, timeout=10)
        else:
            resp = requests.post(TRANSMISSION_URL, headers=headers, json=data, timeout=10)
        if resp.status_code == 409:
            sid = resp.headers.get("X-Transmission-Session-Id", "")
            return _transmission_rpc(method, data, sid)
        return resp
    except Exception as e:
        return None

def _ssdp_discover(st="ssdp:all", timeout=3, mx=3):
    """Send SSDP M-SEARCH and return list of {location, st, usn, server}."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: %d\r\n"
        "ST: %s\r\n"
        "\r\n" % (mx, st)
    )
    results = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.settimeout(timeout)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_BROADCAST'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(msg.encode(), ("239.255.255.250", 1900))
        while True:
            try:
                data, addr = s.recvfrom(4096)
                text = data.decode("utf-8", errors="replace")
                info = {"ip": addr[0], "location": "", "st": "", "usn": "", "server": ""}
                for line in text.splitlines():
                    ll = line.lower().strip()
                    if ll.startswith("location:"):
                        info["location"] = line.split(":", 1)[1].strip()
                    elif ll.startswith("st:"):
                        info["st"] = line.split(":", 1)[1].strip()
                    elif ll.startswith("usn:"):
                        info["usn"] = line.split(":", 1)[1].strip()
                    elif ll.startswith("server:"):
                        info["server"] = line.split(":", 1)[1].strip()
                if info["location"]:
                    results.append(info)
            except socket.timeout:
                break
        s.close()
    except Exception:
        pass
    return results

def _fetch_xml(url, timeout=5):
    """Fetch and parse XML from a URL."""
    try:
        import xml.etree.ElementTree as ET
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 2
        )
        if r.stdout.strip():
            return ET.fromstring(r.stdout)
    except Exception:
        pass
    return None

def _parse_device_description(xml_root):
    """Extract device info and services from UPnP device description XML."""
    ns = {
        "d": "urn:schemas-upnp-org:device-1-0",
        "s": "urn:schemas-upnp-org:service-1-0"
    }
    info = {"manufacturer": "", "modelName": "", "friendlyName": "", "services": []}
    try:
        dev = xml_root.find(".//d:device", ns)
        if dev is not None:
            mfr = dev.find("d:manufacturer", ns)
            if mfr is not None and mfr.text: info["manufacturer"] = mfr.text.strip()
            model = dev.find("d:modelName", ns)
            if model is not None and model.text: info["modelName"] = model.text.strip()
            name = dev.find("d:friendlyName", ns)
            if name is not None and name.text: info["friendlyName"] = name.text.strip()
        for svc in xml_root.findall(".//d:service", ns):
            svc_type = svc.find("d:serviceType", ns)
            ctrl_url = svc.find("d:controlURL", ns)
            if svc_type is not None and ctrl_url is not None:
                info["services"].append({
                    "type": svc_type.text or "",
                    "controlURL": ctrl_url.text or ""
                })
    except Exception:
        pass
    return info

def _upnp_browse(control_url, object_id="0", browse_flag="BrowseDirectChildren"):
    """Send UPnP ContentDirectory Browse SOAP request."""
    soap_body = '<?xml version="1.0" encoding="utf-8"?>'
    soap_body += '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    soap_body += '<s:Body>'
    soap_body += '<u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
    soap_body += '<ObjectID>%s</ObjectID>' % xml_escape(object_id)
    soap_body += '<BrowseFlag>%s</BrowseFlag>' % browse_flag
    soap_body += '<Filter>*</Filter>'
    soap_body += '<StartingIndex>0</StartingIndex>'
    soap_body += '<RequestedCount>500</RequestedCount>'
    soap_body += '<SortCriteria></SortCriteria>'
    soap_body += '</u:Browse>'
    soap_body += '</s:Body>'
    soap_body += '</s:Envelope>'
    try:
        import xml.etree.ElementTree as ET
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "10",
             "-H", "Content-Type: text/xml; charset=\"utf-8\"",
             "-H", 'SOAPAction: "urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
             "-d", soap_body,
             control_url],
            capture_output=True, text=True, timeout=15
        )
        if r.stdout.strip():
            root = ET.fromstring(r.stdout)
            result_el = root.find(".//{urn:schemas-upnp-org:service:ContentDirectory:1}Result")
            if result_el is None:
                result_el = root.find(".//Result")
            if result_el is not None and result_el.text:
                import html as html_mod
                raw = html_mod.unescape(result_el.text.strip())
                didl = ET.fromstring(raw)
                items = []
                for child in didl:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    item = _parse_didl_item(child, tag)
                    if item:
                        items.append(item)
                return items
    except Exception:
        pass
    return []

def _parse_didl_item(elem, tag):
    """Parse a DIDL-Lite item or container into a dict."""
    ns_didl = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
    ns_dc = "http://purl.org/dc/elements/1.1/"
    ns_upnp = "urn:schemas-upnp-org:metadata-1-0/upnp/"

    def _find(el, local):
        for child in el:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == local:
                return child.text or ""
        return ""

    obj_id = elem.get("id", "")
    title = _find(elem, "title")
    creator = _find(elem, "creator")
    upnp_class = _find(elem, "class")
    res = None
    for child in elem:
        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if ctag == "res":
            res = child
            break

    is_container = "container" in upnp_class.lower() if upnp_class else tag.lower() == "container"
    item_type = "folder" if is_container else "file"

    mime = res.get("protocolInfo", "") if res is not None else ""
    file_type = ""
    if "video" in mime: file_type = "video"
    elif "audio" in mime: file_type = "audio"
    elif "image" in mime: file_type = "image"

    size = ""
    if res is not None:
        s = res.get("size", "")
        if s and s.isdigit():
            b = int(s)
            if b > 1073741824: size = "%.1f GB" % (b / 1073741824)
            elif b > 1048576: size = "%.1f MB" % (b / 1048576)
            elif b > 1024: size = "%.1f KB" % (b / 1024)
            else: size = "%d B" % b

    duration = ""
    if res is not None:
        d = res.get("duration", "")
        if d: duration = d

    children = elem.get("childCount", "")

    return {
        "id": obj_id,
        "name": title or "Unknown",
        "type": item_type,
        "fileType": file_type,
        "size": size,
        "duration": duration,
        "creator": creator,
        "children": children,
        "res_url": res.text.strip() if res is not None and res.text else "",
    }

def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _upnp_soap(ip, port, control_url, action, params_xml="", timeout=8):
    """Send a SOAP request to a UPnP device."""
    import xml.etree.ElementTree as ET
    soap = '<?xml version="1.0" encoding="utf-8"?>'
    soap += '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    soap += '<s:Body><u:%s xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">%s</u:%s></s:Body>' % (action, params_xml, action)
    soap += '</s:Envelope>'
    url = "http://%s:%d%s" % (ip, port, control_url)
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout),
             "-H", "Content-Type: text/xml; charset=\"utf-8\"",
             "-H", 'SOAPAction: "urn:schemas-upnp-org:service:AVTransport:1#%s"' % action,
             "-d", soap, url],
            capture_output=True, text=True, timeout=timeout + 2
        )
        if r.stdout.strip():
            return ET.fromstring(r.stdout)
    except Exception:
        pass
    return None

def _upnp_discover_renderers():
    """Discover UPnP MediaRenderer devices."""
    devices = []
    seen = set()
    for info in _ssdp_discover(st="urn:schemas-upnp-org:device:MediaRenderer:1", timeout=3):
        loc = info.get("location", "")
        if not loc or loc in seen:
            continue
        seen.add(loc)
        xml = _fetch_xml(loc)
        if xml is not None:
            dinfo = _parse_device_description(xml)
            parsed = urllib.parse.urlparse(loc)
            ip = info["ip"]
            port = str(parsed.port) if parsed.port else "80"
            friendly = dinfo.get("friendlyName") or info.get("server", "") or ip
            ctrl = ""
            for svc in dinfo.get("services", []):
                if "AVTransport" in svc.get("type", ""):
                    ctrl = svc.get("controlURL", "")
                    break
            if ctrl:
                devices.append({
                    "name": friendly, "ip": ip, "port": port,
                    "manufacturer": dinfo.get("manufacturer", ""),
                    "modelName": dinfo.get("modelName", ""),
                    "controlURL": ctrl, "location": loc,
                })
    return devices

def _upnp_discover_servers():
    """Discover UPnP MediaServer devices."""
    devices = []
    seen = set()
    for info in _ssdp_discover(st="urn:schemas-upnp-org:device:MediaServer:1", timeout=3):
        loc = info.get("location", "")
        if not loc or loc in seen:
            continue
        seen.add(loc)
        xml = _fetch_xml(loc)
        if xml is not None:
            dinfo = _parse_device_description(xml)
            parsed = urllib.parse.urlparse(loc)
            ip = info["ip"]
            port = str(parsed.port) if parsed.port else "80"
            friendly = dinfo.get("friendlyName") or ip
            devices.append({
                "name": friendly, "ip": ip, "port": port,
                "manufacturer": dinfo.get("manufacturer", ""),
                "location": loc,
            })
    return devices

def _alarm_scheduler():
    import datetime as _dt
    fired = set()
    while True:
        time.sleep(15)
        try:
            now = _dt.datetime.now()
            weekday = now.isoweekday()
            current = now.strftime("%H:%M")
            alarms = _load_alarms()
            for a in alarms:
                if not a.get("enabled"):
                    continue
                if a["time"] != current:
                    fired.discard(a["id"])
                    continue
                if weekday not in a.get("days", []):
                    continue
                if a["id"] in fired:
                    continue
                fired.add(a["id"])
                fpath = a.get("file", "")
                if fpath and os.path.exists(fpath):
                    try:
                        ext = os.path.splitext(fpath)[1].lower()
                        if ext == ".mp3":
                            subprocess.Popen(["mpg123", "-q", fpath])
                        else:
                            subprocess.Popen(["aplay", "-q", fpath])
                    except:
                        pass
                if len(fired) > 100:
                    fired.clear()
        except:
            pass

threading.Thread(target=_alarm_scheduler, daemon=True).start()


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    can_edit = ctx.can_edit
    _cmd = ctx._cmd
    service_state = ctx.service_state
    page_data = ctx.page_data

    @app.route("/system/iptv/add", methods=["POST"])
    @can_edit
    @login_required
    def system_iptv_add():

        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()

        if not name or not url:
            return redirect(url_for("system"))

        # §8.8 (01.10.2026): плейлист скачивается сервером
        # (update-iptv.sh/curl) — принимаем только http/https;
        # file://, gopher:// и прочие схемы отсекаем. Внутренние
        # адреса допустимы (LAN-модель, §8.8 принята).
        _parsed = urllib.parse.urlparse(url)
        if _parsed.scheme not in ("http", "https") or not _parsed.netloc:
            return redirect(url_for("system"))

        import re as _re
        file = _re.sub(r'[^a-zA-Z0-9_-]', '_', name).strip('_').lower() or "playlist"

        playlists = load_iptv_playlists()

        playlists.append({
            "name": name,
            "url": url,
            "file": file,
            "enabled": True
        })

        save_iptv_playlists(playlists)

        return redirect(url_for("system"))

    @app.route("/system/iptv/delete/<int:index>", methods=["POST"])
    @can_edit
    @login_required
    def system_iptv_delete(index):

        playlists = load_iptv_playlists()

        if 0 <= index < len(playlists):
            playlists.pop(index)
            save_iptv_playlists(playlists)

        return redirect(url_for("system"))

    @app.route("/system/iptv/toggle/<int:index>", methods=["POST"])
    @can_edit
    @login_required
    def system_iptv_toggle(index):

        playlists = load_iptv_playlists()

        if 0 <= index < len(playlists):

            playlists[index]["enabled"] = not bool(
                playlists[index].get("enabled", False)
            )

            save_iptv_playlists(playlists)

        return redirect(url_for("system"))

    @app.route("/system/iptv/update/<int:index>", methods=["POST"])
    @can_edit
    @login_required
    def system_iptv_update(index):

        playlists = load_iptv_playlists()

        if not (0 <= index < len(playlists)):
            return redirect(url_for("system"))

        if not playlists[index].get("enabled", False):
            return redirect(url_for("system"))

        unit = f"update-iptv-one-{index}"

        if service_state(unit) == "active":
            return redirect(url_for("system"))

        subprocess.Popen(
            [
                "systemd-run",
                "--unit=" + unit,
                "--property=Type=oneshot",
                "/usr/local/sbin/update-iptv-one-worker.sh",
                str(index)
            ]
        )

        return redirect(url_for("system"))

    @app.route("/system/iptv", methods=["POST"])
    @admin_required
    @login_required
    def system_iptv():

        subprocess.Popen(
            ["systemctl", "start", "update-iptv.service"]
        )

        return redirect(url_for("system"))

    @app.route("/api/alarm/files")
    @login_required
    def api_alarm_files():
        files = []
        for d in ["/media/alarm", "/media"]:
            try:
                for fn in os.listdir(d):
                    if fn.lower().endswith((".mp3", ".wav", ".ogg", ".flac")):
                        files.append({"name": fn, "path": os.path.join(d, fn), "dir": d})
            except:
                pass
        return jsonify(files)

    @app.route("/api/alarm/volume", methods=["GET"])
    @login_required
    def api_alarm_volume_get():
        try:
            out = _cmd(["amixer", "get", "Line Out"], timeout=5)
            import re
            m = re.search(r"\[(\d+)%\]", out)
            return jsonify({"volume": int(m.group(1)) // 10 if m else 5})
        except:
            return jsonify({"volume": 5})

    @app.route("/api/alarm/volume", methods=["POST"])
    @can_edit
    @login_required
    def api_alarm_volume_set():
        data = request.get_json() or {}
        vol = int(data.get("volume", 5))
        pct = max(0, min(100, vol * 10))
        try:
            _cmd(["amixer", "set", "Line Out", "%d%%" % pct], timeout=5)
            return jsonify({"ok": True, "volume": vol})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/alarm/play", methods=["POST"])
    @can_edit
    @login_required
    def api_alarm_play():
        global _alarm_process
        data = request.get_json() or {}
        path = data.get("path", "")
        if not path or not os.path.exists(path):
            return jsonify({"error": "file not found"}), 400
        try:
            if _alarm_process and _alarm_process.poll() is None:
                _alarm_process.terminate()
            ext = os.path.splitext(path)[1].lower()
            if ext == ".mp3":
                _alarm_process = subprocess.Popen(["mpg123", "-q", path])
            else:
                _alarm_process = subprocess.Popen(["aplay", "-q", path])
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/alarm/stop", methods=["POST"])
    @can_edit
    @login_required
    def api_alarm_stop():
        global _alarm_process
        try:
            if _alarm_process and _alarm_process.poll() is None:
                _alarm_process.terminate()
                _alarm_process = None
            subprocess.Popen(["pkill", "-f", "mpg123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen(["pkill", "-f", "aplay"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return jsonify({"ok": True})
        except:
            return jsonify({"ok": True})

    @app.route("/api/alarms")
    @login_required
    def api_alarms_list():
        return jsonify(_load_alarms())

    @app.route("/api/alarms", methods=["POST"])
    @can_edit
    @login_required
    def api_alarms_add():
        data = request.get_json() or {}
        alarms = _load_alarms()
        alarm = {
            "id": int(time.time() * 1000),
            "time": data.get("time", "07:00"),
            "file": data.get("file", ""),
            "enabled": True,
            "days": data.get("days", [1,2,3,4,5]),
        }
        alarms.append(alarm)
        _save_alarms(alarms)
        return jsonify({"ok": True, "alarm": alarm})

    @app.route("/api/alarms/<int:aid>", methods=["DELETE"])
    @can_edit
    @login_required
    def api_alarms_delete(aid):
        alarms = _load_alarms()
        alarms = [a for a in alarms if a["id"] != aid]
        _save_alarms(alarms)
        return jsonify({"ok": True})

    @app.route("/api/alarms/<int:aid>/toggle", methods=["POST"])
    @can_edit
    @login_required
    def api_alarms_toggle(aid):
        alarms = _load_alarms()
        for a in alarms:
            if a["id"] == aid:
                a["enabled"] = not a.get("enabled", True)
                break
        _save_alarms(alarms)
        return jsonify({"ok": True})

    @app.route("/api/radio/stations")
    @login_required
    def api_radio_stations():
        global _radio_stations, _radio_index
        playlist = request.args.get("playlist", "")
        if playlist:
            path = os.path.join(IPTV_DIR, playlist)
            stations = _parse_m3u(path) if os.path.exists(path) else []
        else:
            stations = _load_radio_stations()
        _radio_stations = stations
        _radio_index = -1
        return jsonify({"stations": stations[:500], "total": len(stations)})

    @app.route("/api/radio/playlists")
    @login_required
    def api_radio_playlists():
        playlists = []
        try:
            for fn in sorted(os.listdir(IPTV_DIR)):
                path = os.path.join(IPTV_DIR, fn)
                if os.path.isfile(path) and not fn.startswith("."):
                    count = len(_parse_m3u(path))
                    playlists.append({"file": fn, "count": count})
        except:
            pass
        return jsonify(playlists)

    @app.route("/api/radio/play", methods=["POST"])
    @can_edit
    @login_required
    def api_radio_play():
        global _radio_index
        data = request.get_json() or {}
        url = data.get("url", "")
        index = data.get("index")
        if index is not None:
            _radio_index = int(index)
            if 0 <= _radio_index < len(_radio_stations):
                url = _radio_stations[_radio_index]["url"]
        if not url:
            return jsonify({"error": "no url"}), 400
        _radio_play(url, volume=data.get("volume"))
        return jsonify({"ok": True})

    @app.route("/api/radio/stop", methods=["POST"])
    @can_edit
    @login_required
    def api_radio_stop():
        _radio_stop()
        return jsonify({"ok": True})

    @app.route("/api/radio/next", methods=["POST"])
    @can_edit
    @login_required
    def api_radio_next():
        global _radio_index
        data = request.get_json() or {}
        volume = data.get("volume")
        if _radio_stations:
            _radio_index = (_radio_index + 1) % len(_radio_stations)
            _radio_play(_radio_stations[_radio_index]["url"], volume=volume)
            return jsonify({"ok": True, "index": _radio_index, "station": _radio_stations[_radio_index]})
        return jsonify({"error": "no stations"}), 400

    @app.route("/api/radio/prev", methods=["POST"])
    @can_edit
    @login_required
    def api_radio_prev():
        global _radio_index
        data = request.get_json() or {}
        volume = data.get("volume")
        if _radio_stations:
            _radio_index = (_radio_index - 1) % len(_radio_stations)
            _radio_play(_radio_stations[_radio_index]["url"], volume=volume)
            return jsonify({"ok": True, "index": _radio_index, "station": _radio_stations[_radio_index]})
        return jsonify({"error": "no stations"}), 400

    @app.route("/api/radio/status")
    @login_required
    def api_radio_status():
        playing = _radio_process is not None and _radio_process.poll() is None
        station = _radio_stations[_radio_index] if _radio_stations and 0 <= _radio_index < len(_radio_stations) else None
        return jsonify({"playing": playing, "index": _radio_index, "station": station})

    @app.route("/api/player/browse")
    @login_required
    def api_player_browse():
        path = request.args.get("path", MEDIA_DIR)
        if not os.path.isdir(path):
            return jsonify({"error": "not a directory"}), 400
        entries = []
        try:
            for entry in sorted(os.scandir(path)):
                if entry.is_dir() and not entry.name.startswith("."):
                    count = len(_scan_audio_files(entry.path))
                    entries.append({"name": entry.name, "path": entry.path, "type": "dir", "count": count})
                elif entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in AUDIO_EXTS:
                        entries.append({"name": os.path.splitext(entry.name)[0], "path": entry.path, "type": "file"})
        except:
            pass
        parent = os.path.dirname(path) if path != MEDIA_DIR else None
        return jsonify({"path": path, "parent": parent, "entries": entries})

    @app.route("/api/player/playlists")
    @login_required
    def api_player_playlists():
        playlists = []
        try:
            os.makedirs(PLAYLISTS_DIR, exist_ok=True)
            for fn in sorted(os.listdir(PLAYLISTS_DIR)):
                if fn.endswith(".json"):
                    path = os.path.join(PLAYLISTS_DIR, fn)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        playlists.append({
                            "file": fn,
                            "name": data.get("name", fn[:-5]),
                            "count": len(data.get("files", []))
                        })
                    except:
                        pass
        except:
            pass
        return jsonify(playlists)

    @app.route("/api/player/playlist", methods=["POST"])
    @login_required
    @admin_required
    def api_player_save_playlist():
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        files = data.get("files", [])
        if not name:
            return jsonify({"error": "name required"}), 400
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip()
        if not safe_name:
            return jsonify({"error": "invalid name"}), 400
        filename = safe_name + ".json"
        path = os.path.join(PLAYLISTS_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"name": name, "files": files}, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "file": filename})

    @app.route("/api/player/playlist/<filename>", methods=["DELETE"])
    @login_required
    @admin_required
    def api_player_delete_playlist(filename):
        path = os.path.join(PLAYLISTS_DIR, filename)
        if os.path.exists(path):
            os.remove(path)
        return jsonify({"ok": True})

    @app.route("/api/player/load")
    @login_required
    def api_player_load_playlist():
        global _player_stations, _player_index, _player_order
        filename = request.args.get("file", "")
        path = os.path.join(PLAYLISTS_DIR, filename)
        if not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stations = []
        for fp in data.get("files", []):
            if os.path.isfile(fp):
                stations.append({"name": os.path.splitext(os.path.basename(fp))[0], "path": fp})
        _player_stations = stations
        _player_index = -1
        _player_order = list(range(len(stations)))
        return jsonify({"stations": stations[:500], "total": len(stations), "name": data.get("name", filename)})

    @app.route("/api/player/play", methods=["POST"])
    @can_edit
    @login_required
    def api_player_play():
        global _player_index
        data = request.get_json() or {}
        path = data.get("path", "")
        index = data.get("index")
        volume = data.get("volume")
        if index is not None:
            _player_index = int(index)
            if 0 <= _player_index < len(_player_stations):
                path = _player_stations[_player_index]["path"]
        if not path or not os.path.isfile(path):
            return jsonify({"error": "file not found"}), 400
        _player_play(path, volume=volume)
        return jsonify({"ok": True})

    @app.route("/api/player/stop", methods=["POST"])
    @can_edit
    @login_required
    def api_player_stop():
        _player_stop()
        return jsonify({"ok": True})

    @app.route("/api/player/next", methods=["POST"])
    @can_edit
    @login_required
    def api_player_next():
        global _player_index
        data = request.get_json() or {}
        volume = data.get("volume")
        if _player_stations:
            if _player_random:
                import random
                _player_index = random.randint(0, len(_player_stations) - 1)
            else:
                _player_index = (_player_index + 1) % len(_player_stations)
            _player_play(_player_stations[_player_index]["path"], volume=volume)
            return jsonify({"ok": True, "index": _player_index, "station": _player_stations[_player_index]})
        return jsonify({"error": "no stations"}), 400

    @app.route("/api/player/prev", methods=["POST"])
    @can_edit
    @login_required
    def api_player_prev():
        global _player_index
        data = request.get_json() or {}
        volume = data.get("volume")
        if _player_stations:
            if _player_random:
                import random
                _player_index = random.randint(0, len(_player_stations) - 1)
            else:
                _player_index = (_player_index - 1) % len(_player_stations)
            _player_play(_player_stations[_player_index]["path"], volume=volume)
            return jsonify({"ok": True, "index": _player_index, "station": _player_stations[_player_index]})
        return jsonify({"error": "no stations"}), 400

    @app.route("/api/player/random", methods=["POST"])
    @can_edit
    @login_required
    def api_player_random():
        global _player_random
        data = request.get_json() or {}
        _player_random = bool(data.get("random", False))
        return jsonify({"ok": True, "random": _player_random})

    @app.route("/api/player/status")
    @login_required
    def api_player_status():
        playing = _player_process is not None and _player_process.poll() is None
        station = _player_stations[_player_index] if _player_stations and 0 <= _player_index < len(_player_stations) else None
        return jsonify({"playing": playing, "index": _player_index, "station": station, "random": _player_random})

    @app.route("/api/cameras")
    @login_required
    def api_cameras_list():
        return jsonify(_load_cameras())

    @app.route("/api/cameras", methods=["POST"])
    @login_required
    @admin_required
    def api_cameras_add():
        data = request.get_json() or {}
        url = data.get("url", "").strip()
        name = data.get("name", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        cameras = _load_cameras()
        cam_id = max((c["id"] for c in cameras), default=0) + 1
        cam = {"id": cam_id, "name": name or "Камера " + str(cam_id), "url": url}
        cameras.append(cam)
        _save_cameras(cameras)
        _start_camera_stream(cam)
        return jsonify({"ok": True, "camera": cam})

    @app.route("/api/cameras/<int:cam_id>", methods=["PUT"])
    @login_required
    @admin_required
    def api_cameras_update(cam_id):
        data = request.get_json() or {}
        cameras = _load_cameras()
        for c in cameras:
            if c["id"] == cam_id:
                if "name" in data:
                    c["name"] = data["name"]
                if "url" in data:
                    c["url"] = data["url"]
                _save_cameras(cameras)
                _start_camera_stream(c)
                return jsonify({"ok": True, "camera": c})
        return jsonify({"error": "not found"}), 404

    @app.route("/api/cameras/<int:cam_id>", methods=["DELETE"])
    @login_required
    @admin_required
    def api_cameras_delete(cam_id):
        _stop_camera_stream(cam_id)
        cameras = _load_cameras()
        cameras = [c for c in cameras if c["id"] != cam_id]
        _save_cameras(cameras)
        return jsonify({"ok": True})

    @app.route("/api/cameras/<int:cam_id>/start", methods=["POST"])
    @can_edit
    @login_required
    def api_cameras_start(cam_id):
        cameras = _load_cameras()
        for c in cameras:
            if c["id"] == cam_id:
                _start_camera_stream(c)
                return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    @app.route("/api/cameras/<int:cam_id>/stop", methods=["POST"])
    @can_edit
    @login_required
    def api_cameras_stop(cam_id):
        _stop_camera_stream(cam_id)
        return jsonify({"ok": True})

    @app.route("/api/cameras/start_all", methods=["POST"])
    @can_edit
    @login_required
    def api_cameras_start_all():
        for c in _load_cameras():
            _start_camera_stream(c)
        return jsonify({"ok": True})

    @app.route("/api/cameras/stop_all", methods=["POST"])
    @can_edit
    @login_required
    def api_cameras_stop_all():
        for cam_id in list(_camera_processes.keys()):
            _stop_camera_stream(cam_id)
        return jsonify({"ok": True})

    @app.route("/api/cameras/stream/<int:cam_id>")
    @login_required
    def api_cameras_stream(cam_id):
        info = _camera_processes.get(cam_id)
        if not info or info["proc"].poll() is not None:
            cameras = _load_cameras()
            for c in cameras:
                if c["id"] == cam_id:
                    _start_camera_stream(c)
                    info = _camera_processes.get(cam_id)
                    break
        if info:
            return redirect("http://127.0.0.1:" + str(info["port"]) + "/stream")
        return "Camera offline", 503

    @app.route("/api/transmission")
    @admin_required
    def api_transmission_get():
        try:
            with open(TRANSMISSION_CONF, "r") as f:
                data = json.load(f)
            return jsonify({
                "username": data.get("rpc-username", ""),
                "password": data.get("rpc-password", "")
            })
        except Exception:
            return jsonify({"username": "", "password": ""})

    @app.route("/api/transmission", methods=["POST"])
    @admin_required
    def api_transmission_save():
        data = request.get_json() or {}
        pw = data.get("password", "").strip()
        if not pw:
            return jsonify({"error": "password required"}), 400
        try:
            with open(TRANSMISSION_CONF, "r") as f:
                conf = json.load(f)
            conf["rpc-username"] = "transmission"
            conf["rpc-password"] = pw
            with open(TRANSMISSION_CONF, "w") as f:
                json.dump(conf, f, indent=2)
            subprocess.Popen(["systemctl", "restart", "transmission-daemon"])
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/transmission/torrents")
    @login_required
    def api_transmission_torrents():
        resp = _transmission_rpc("POST", {
            "method": "torrent-get",
            "arguments": {"fields": ["id", "name", "status", "percentDone", "rateDownload", "rateUpload", "eta", "totalSize", "doneDate", "peersConnected", "peersFrom", "uploadRatio", "addedDate", "error", "errorString"]}
        })
        if resp is None or not resp.ok:
            return {"ok": False, "error": "Transmission not available"}
        try:
            result = resp.json()
            torrents = []
            for t in result.get("arguments", {}).get("torrents", []):
                eta = t.get("eta", -1)
                if eta < 0:
                    eta = 0
                torrents.append({
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "status": t.get("status", 0),
                    "progress": round(t.get("percentDone", 0) * 100, 1),
                    "speed_down": t.get("rateDownload", 0),
                    "speed_up": t.get("rateUpload", 0),
                    "eta": eta,
                    "size": t.get("totalSize", 0),
                    "total_done": round(t.get("percentDone", 0) * t.get("totalSize", 0)),
                    "seeds": t.get("peersConnected", 0),
                    "peers": sum(t.get("peersFrom", {}).values()),
                    "ratio": round(t.get("uploadRatio", 0), 2),
                    "added": datetime.fromtimestamp(t.get("addedDate", 0)).strftime("%d.%m.%Y %H:%M") if t.get("addedDate") else "",
                    "error": t.get("error", 0),
                    "error_string": t.get("errorString", "")
                })
            stats_resp = _transmission_rpc("POST", {
                "method": "session-stats",
                "arguments": {}
            })
            stats = {}
            if stats_resp and stats_resp.ok:
                s = stats_resp.json().get("arguments", {})
                stats = {
                    "speed_down_total": s.get("downloadSpeed", 0),
                    "speed_up_total": s.get("uploadSpeed", 0),
                    "active": s.get("activeTorrentCount", 0)
                }
            return {"ok": True, "torrents": torrents, "stats": stats}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/transmission/add", methods=["POST"])
    @can_edit
    @login_required
    def api_transmission_add():
        url = request.json.get("url", "").strip()
        if not url:
            return {"ok": False, "error": "Введите magnet ссылку или URL"}
        resp = _transmission_rpc("POST", {
            "method": "torrent-add",
            "arguments": {"filename": url}
        })
        if resp and resp.ok:
            return {"ok": True}
        return {"ok": False, "error": "Transmission не доступен"}

    @app.route("/api/transmission/action", methods=["POST"])
    @can_edit
    @login_required
    def api_transmission_action():
        data = request.json
        tid = data.get("id")
        action = data.get("action")
        method_map = {
            "start": "torrent-start",
            "stop": "torrent-stop",
            "remove": "torrent-remove",
            "verify": "torrent-verify"
        }
        method = method_map.get(action)
        if not method:
            return {"ok": False, "error": "Unknown action"}
        args = {"ids": [tid]}
        if action == "remove":
            args["delete-local-data"] = False
        resp = _transmission_rpc("POST", {"method": method, "arguments": args})
        if resp and resp.ok:
            return {"ok": True}
        return {"ok": False, "error": "Transmission не доступен"}

    @app.route("/api/transmission/queue", methods=["POST"])
    @can_edit
    @login_required
    def api_transmission_queue():
        data = request.json
        tid = data.get("id")
        action = data.get("action")
        method_map = {
            "up": "queue-move-up",
            "down": "queue-move-down",
            "top": "queue-move-to-top",
            "bottom": "queue-move-to-bottom"
        }
        method = method_map.get(action)
        if not method:
            return {"ok": False, "error": "Unknown action"}
        resp = _transmission_rpc("POST", {"method": method, "arguments": {"ids": [tid]}})
        if resp and resp.ok:
            return {"ok": True}
        return {"ok": False, "error": "Transmission не доступен"}

    @app.route("/apps/downloads")
    @login_required
    def app_downloads():
        return render_template("apps/downloads.html", **page_data())

    @app.route("/api/dlna/scan")
    @login_required
    def api_dlna_scan():
        try:
            devices = []
            # SSDP M-SEARCH broad discovery
            ssdp_targets = [
                "urn:schemas-upnp-org:device:MediaServer:1",
                "urn:schemas-upnp-org:device:MediaRenderer:1",
                "urn:schemas-upnp-org:device:Basic:1",
                "ssdp:all",
            ]
            seen_locations = set()
            for target in ssdp_targets:
                for info in _ssdp_discover(st=target, timeout=2):
                    loc = info["location"]
                    if loc and loc not in seen_locations:
                        seen_locations.add(loc)
                        xml = _fetch_xml(loc)
                        if xml is not None:
                            dinfo = _parse_device_description(xml)
                            ip = info["ip"]
                            parsed = urllib.parse.urlparse(loc)
                            port = str(parsed.port) if parsed.port else "80"
                            friendly = dinfo.get("friendlyName") or info.get("server", "") or info.get("usn", "").split("::")[0]
                            devices.append({
                                "name": friendly, "type": "upnp",
                                "ip": ip, "port": port,
                                "manufacturer": dinfo.get("manufacturer", ""),
                                "modelName": dinfo.get("modelName", ""),
                                "location": loc,
                                "services": dinfo.get("services", []),
                                "uuid": info.get("usn", ""),
                            })
            # Also try avahi-browse
            for svc_type in ["_smb._tcp", "_dlna-headless._tcp"]:
                r = subprocess.run(
                    ["/usr/bin/avahi-browse", "-t", "-r", "-p", svc_type],
                    capture_output=True, text=True, timeout=8
                )
                for line in r.stdout.splitlines():
                    parts = line.split(";")
                    if len(parts) >= 8 and parts[0] == "=":
                        name = parts[3]
                        ip = parts[7]
                        port = parts[8] if len(parts) > 8 else ""
                        key = "%s:%s" % (ip, name)
                        if not any(d["ip"] == ip and d["name"] == name for d in devices):
                            devices.append({
                                "name": name, "type": svc_type.replace(".", ""),
                                "ip": ip, "port": port,
                                "manufacturer": "", "modelName": "",
                                "location": "", "services": [],
                                "uuid": parts[5] if len(parts) > 5 else "",
                            })
            return {"ok": True, "devices": devices}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/dlna/content")
    @login_required
    def api_dlna_content():
        ip = request.args.get("ip", "")
        path = request.args.get("path", "/")
        object_id = request.args.get("id", "0")
        if not ip:
            return {"ok": False, "error": "No IP"}
        try:
            devices = []
            # Re-discover device to get its location/services
            for info in _ssdp_discover(st="urn:schemas-upnp-org:device:MediaServer:1", timeout=2):
                if info["ip"] == ip:
                    loc = info["location"]
                    if loc:
                        xml = _fetch_xml(loc)
                        if xml is not None:
                            dinfo = _parse_device_description(xml)
                            for svc in dinfo.get("services", []):
                                if "ContentDirectory" in svc.get("type", ""):
                                    parsed = urllib.parse.urlparse(loc)
                                    base = "%s://%s:%s" % (parsed.scheme, parsed.hostname, parsed.port or 80)
                                    ctrl_url = base + svc["controlURL"]
                                    items = _upnp_browse(ctrl_url, object_id)
                                    return {"ok": True, "items": items, "path": path, "objectId": object_id}
                            return {"ok": True, "items": [], "info": "ContentDirectory service not found"}
                        return {"ok": True, "items": [], "info": "Could not fetch device description"}
            # If not found via SSDP, try gssdp-discover
            r = subprocess.run(
                ["/usr/bin/gssdp-discover", "--timeout=3"],
                capture_output=True, text=True, timeout=8
            )
            return {"ok": True, "items": [], "info": r.stdout[:1000]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/dlna/file_url")
    @login_required
    def api_dlna_file_url():
        ip = request.args.get("ip", "")
        object_id = request.args.get("id", "")
        if not ip or not object_id:
            return {"ok": False, "error": "Missing ip or id"}
        for info in _ssdp_discover(st="urn:schemas-upnp-org:device:MediaServer:1", timeout=2):
            if info["ip"] == ip:
                loc = info.get("location", "")
                if loc:
                    xml = _fetch_xml(loc)
                    if xml:
                        dinfo = _parse_device_description(xml)
                        for svc in dinfo.get("services", []):
                            if "ContentDirectory" in svc.get("type", ""):
                                parsed = urllib.parse.urlparse(loc)
                                base = "%s://%s:%s" % (parsed.scheme, parsed.hostname, parsed.port or 80)
                                ctrl_url = base + svc["controlURL"]
                                items = _upnp_browse(ctrl_url, object_id)
                                if items and len(items) > 0:
                                    item = items[0]
                                    res_url = item.get("res_url", "")
                                    if res_url:
                                        return {"ok": True, "url": res_url}
                                    return {"ok": True, "url": "", "info": "No direct URL available", "item": item}
                                return {"ok": False, "error": "Item not found"}
        return {"ok": False, "error": "Server not found"}

    @app.route("/api/upnp/renderers")
    @login_required
    def api_upnp_renderers():
        try:
            renderers = _upnp_discover_renderers()
            return {"ok": True, "renderers": renderers}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/upnp/servers")
    @login_required
    def api_upnp_servers():
        try:
            servers = _upnp_discover_servers()
            return {"ok": True, "servers": servers}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/upnp/play", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_play():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        url = data.get("url", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        if not ip or not url:
            return {"ok": False, "error": "Missing ip or url"}
        params = "<InstanceID>0</InstanceID><CurrentURI>%s</CurrentURI><CurrentURIMetaData></CurrentURIMetaData>" % xml_escape(url)
        result = _upnp_soap(ip, port, ctrl, "SetAVTransportURI", params)
        if result is not None:
            _upnp_soap(ip, port, ctrl, "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
            return {"ok": True}
        return {"ok": False, "error": "SOAP failed"}

    @app.route("/api/upnp/pause", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_pause():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        params = "<InstanceID>0</InstanceID>"
        _upnp_soap(ip, port, ctrl, "Pause", params)
        return {"ok": True}

    @app.route("/api/upnp/stop", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_stop():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        params = "<InstanceID>0</InstanceID>"
        _upnp_soap(ip, port, ctrl, "Stop", params)
        return {"ok": True}

    @app.route("/api/upnp/next", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_next():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        params = "<InstanceID>0</InstanceID><Speed>1</Speed>"
        _upnp_soap(ip, port, ctrl, "Next", params)
        return {"ok": True}

    @app.route("/api/upnp/prev", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_prev():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        params = "<InstanceID>0</InstanceID><Speed>1</Speed>"
        _upnp_soap(ip, port, ctrl, "Previous", params)
        return {"ok": True}

    @app.route("/api/upnp/volume", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_volume():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        level = int(data.get("level", 50))
        params = "<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>%d</DesiredVolume>" % level
        _upnp_soap(ip, port, ctrl, "SetVolume", params)
        return {"ok": True}

    @app.route("/api/upnp/mute", methods=["POST"])
    @can_edit
    @login_required
    def api_upnp_mute():
        data = request.json or {}
        ip = data.get("renderer_ip", "")
        port = int(data.get("port", 80))
        ctrl = data.get("control_url", "/AVTransport/control")
        mute = data.get("mute", True)
        params = "<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredMute>%s</DesiredMute>" % ("1" if mute else "0")
        _upnp_soap(ip, port, ctrl, "SetMute", params)
        return {"ok": True}

    @app.route("/api/upnp/status")
    @login_required
    def api_upnp_status():
        ip = request.args.get("renderer_ip", "")
        port = int(request.args.get("port", 80))
        ctrl = request.args.get("control_url", "/AVTransport/control")
        if not ip:
            return {"ok": False, "error": "No IP"}
        try:
            import xml.etree.ElementTree as ET
            result = _upnp_soap(ip, port, ctrl, "GetTransportInfo")
            state = "UNKNOWN"
            if result is not None:
                ns = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "u": "urn:schemas-upnp-org:service:AVTransport:1"}
                ti = result.find(".//u:GetTransportInfoResponse/CurrentTransportState", ns)
                if ti is None:
                    ti = result.find(".//CurrentTransportState")
                if ti is not None:
                    state = ti.text or "UNKNOWN"
            result2 = _upnp_soap(ip, port, ctrl, "GetPositionInfo")
            title = ""
            pos = "00:00:00"
            dur = "00:00:00"
            if result2 is not None:
                ns = {"s": "http://schemas.xmlsoap.org/soap/envelope/", "u": "urn:schemas-upnp-org:service:AVTransport:1"}
                track = result2.find(".//u:GetPositionInfoResponse/TrackTitle", ns)
                if track is None:
                    track = result2.find(".//TrackTitle")
                if track is not None:
                    title = track.text or ""
                rel = result2.find(".//u:GetPositionInfoResponse/RelTime", ns)
                if rel is None:
                    rel = result2.find(".//RelTime")
                if rel is not None:
                    pos = rel.text or "00:00:00"
                d = result2.find(".//u:GetPositionInfoResponse/TrackDuration", ns)
                if d is None:
                    d = result2.find(".//TrackDuration")
                if d is not None:
                    dur = d.text or "00:00:00"
            return {"ok": True, "state": state, "title": title, "position": pos, "duration": dur}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/upnp/browse")
    @login_required
    def api_upnp_browse():
        ip = request.args.get("server_ip", "")
        object_id = request.args.get("id", "0")
        if not ip:
            return {"ok": False, "error": "No IP"}
        for info in _ssdp_discover(st="urn:schemas-upnp-org:device:MediaServer:1", timeout=2):
            if info["ip"] == ip:
                loc = info.get("location", "")
                if loc:
                    xml = _fetch_xml(loc)
                    if xml:
                        dinfo = _parse_device_description(xml)
                        for svc in dinfo.get("services", []):
                            if "ContentDirectory" in svc.get("type", ""):
                                parsed = urllib.parse.urlparse(loc)
                                base = "%s://%s:%s" % (parsed.scheme, parsed.hostname, parsed.port or 80)
                                ctrl_url = base + svc["controlURL"]
                                items = _upnp_browse(ctrl_url, object_id)
                                return {"ok": True, "items": items}
        return {"ok": True, "items": []}

    @app.route("/apps/dlna")
    @login_required
    def app_dlna():
        return render_template("apps/dlna.html", **page_data())

    @app.route("/apps/upnp")
    @login_required
    def app_upnp():
        return render_template("apps/upnp.html", **page_data())

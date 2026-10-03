import json
import os
import re
import socket
import sys
import threading
import time

from flask import request, jsonify, render_template

from core import network as core_net
from core import process as core_process

# C-02: скрипт лежит рядом с кодом (корень репо), путь вычисляется от __file__
# — работает и при --prefix, и в контейнере стенда N6 (BindPaths /opt/...)
_NETWORK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_CHECK_SCRIPT = os.path.join(_NETWORK_ROOT, "network_check.py")
NETWORK_CONFIG = "/etc/lan-discovery/network.json"

_network_config_cache = {"data": None, "ts": 0}

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$")
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _valid_host(host):
    """Хост для сетевых инструментов: имя/IPv4/IPv6.

    Запрещает пробелы, '/', null-байты и ведущий '-' (argument injection
    в ping/host/nslookup/tracepath/traceroute через argv).
    """
    if not isinstance(host, str):
        return False
    host = host.strip()
    if not host or len(host) > 253 or "/" in host or host.startswith("-"):
        return False
    return bool(_HOST_RE.match(host))


def _valid_mac(mac):
    """MAC-адрес для bluetoothctl (строка уходит в stdin команды)."""
    return isinstance(mac, str) and bool(_MAC_RE.match(mac.strip()))

def load_network_config():
    now = time.time()
    if _network_config_cache["data"] is not None and now - _network_config_cache["ts"] < 60:
        return _network_config_cache["data"]
    try:
        with open(NETWORK_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        _network_config_cache["data"] = data
        _network_config_cache["ts"] = now
        return data
    except Exception:
        return {"hosts": _cfg("network", "default_hosts", ["google.com", "ya.ru", "192.168.3.1"])}

def save_network_config(data):
    try:
        os.makedirs(os.path.dirname(NETWORK_CONFIG), exist_ok=True)
        with open(NETWORK_CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _bt_cmd(command, timeout=10):
    """Run a bluetoothctl command non-interactively via stdin pipe."""
    try:
        r = core_process.run(
            ["timeout", str(timeout), "bluetoothctl"],
            input=command + "\nquit\n",
            timeout=timeout + 2
        )
        # Strip ANSI escape codes and bluetoothctl prompt artifacts
        out = r.stdout
        out = re.sub(r'\x1b\[[0-9;]*m', '', out)
        out = re.sub(r'\[[0-9;]*m', '', out)
        return out.strip()
    except Exception:
        return ""


_bt_scan_lock = threading.Lock()


def _spawn_bt_scan():
    """Запустить bluetooth-скан, если предыдущий ещё идёт (P1-8 guard)."""
    if not _bt_scan_lock.acquire(blocking=False):
        return False

    def _run():
        try:
            _bt_cmd("scan on", timeout=35)
            time.sleep(30)
            _bt_cmd("scan off", timeout=5)
        finally:
            _bt_scan_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return True


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    can_edit = ctx.can_edit
    _cmd = ctx._cmd
    _cfg = ctx._cfg
    page_data = ctx.page_data

    @app.route("/api/network/config")
    @login_required
    def api_network_config():
        """Get network test addresses"""
        return jsonify(load_network_config())

    @app.route("/api/network/ifaces")
    @admin_required
    @login_required
    def api_network_ifaces():
        """Сетевые интерфейсы хоста — для выбора в форме скана (№63)."""
        out = []
        try:
            raw = _cmd(["ip", "-br", "-4", "addr", "show"], timeout=5)
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) < 3 or parts[0] == "lo":
                    continue
                out.append({
                    "name": parts[0],
                    "state": parts[1],
                    "ip": parts[2].split("/")[0],
                })
        except Exception as e:
            return jsonify({"ifaces": [], "error": str(e)}), 503
        return jsonify({"ifaces": out})

    @app.route("/api/network/config", methods=["POST"])
    @admin_required
    @login_required
    def api_network_config_save():
        """Save network test addresses"""
        data = request.get_json() or {}
        hosts = data.get("hosts", [])
        if len(hosts) < 3:
            hosts.extend([""] * (3 - len(hosts)))
        save_network_config({"hosts": hosts[:3]})
        return jsonify({"ok": True})

    @app.route("/api/network/check")
    @login_required
    def api_network_check():
        """Check network connectivity"""
        try:
            out = _cmd([sys.executable, NETWORK_CHECK_SCRIPT, "all"], timeout=30)
            return jsonify(json.loads(out))
        except Exception as e:
            return jsonify({"internet": False, "ru_zone": False, "error": str(e)})

    @app.route("/api/network/check_host", methods=["POST"])
    @can_edit
    @login_required
    def api_network_check_host():
        """Check custom host connectivity"""
        data = request.get_json() or {}
        host = data.get("host", "")
        if not host:
            return jsonify({"error": "no host"}), 400
        if not _valid_host(host):
            return jsonify({"error": "invalid host"}), 400
        try:
            out = _cmd([sys.executable, NETWORK_CHECK_SCRIPT, "provider", host.strip()], timeout=15)
            return jsonify(json.loads(out))
        except Exception as e:
            return jsonify({"host": host, "online": False, "error": str(e)})

    @app.route("/api/nettools/ping", methods=["POST"])
    @can_edit
    @login_required
    def api_nettools_ping():
        host = request.json.get("host", "").strip()
        if not host:
            return {"ok": False, "error": "Введите хост"}
        if not _valid_host(host):
            return {"ok": False, "error": "Недопустимый хост"}, 400
        try:
            r = core_process.run(
                ["ping", "-c", "4", "-W", "3", host],
                timeout=20
            )
            return {"ok": True, "output": r.stdout or r.stderr}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/nettools/dns", methods=["POST"])
    @can_edit
    @login_required
    def api_nettools_dns():
        host = request.json.get("host", "").strip()
        if not host:
            return {"ok": False, "error": "Введите хост"}
        if not _valid_host(host):
            return {"ok": False, "error": "Недопустимый хост"}, 400
        try:
            r = core_process.run(
                ["host", host],
                timeout=15
            )
            if r.returncode != 0:
                r = core_process.run(
                    ["nslookup", host],
                    timeout=15
                )
            return {"ok": True, "output": r.stdout or r.stderr}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/nettools/ports", methods=["POST"])
    @can_edit
    @login_required
    def api_nettools_ports():
        data = request.json
        host = data.get("host", "").strip()
        ports_str = data.get("ports", "22,80,443,8080")
        if not host:
            return {"ok": False, "error": "Введите хост"}
        if not _valid_host(host):
            return {"ok": False, "error": "Недопустимый хост"}, 400
        try:
            ports = [int(p.strip()) for p in ports_str.split(",") if p.strip()]
        except ValueError:
            return {"ok": False, "error": "Неверный формат портов"}
        services = {
            21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
            80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS", 445: "SMB",
            993: "IMAPS", 995: "POP3S", 3306: "MySQL", 3389: "RDP",
            5432: "PostgreSQL", 6379: "Redis", 8080: "HTTP-Alt", 8443: "HTTPS-Alt"
        }
        results = []
        for port in ports:
            state = "closed"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                r = s.connect_ex((host, port))
                if r == 0:
                    state = "open"
                s.close()
            except Exception:
                state = "error"
            results.append({
                "port": port,
                "state": state,
                "service": services.get(port, "unknown")
            })
        return {"ok": True, "results": results}

    @app.route("/api/nettools/trace", methods=["POST"])
    @can_edit
    @login_required
    def api_nettools_trace():
        host = request.json.get("host", "").strip()
        if not host:
            return {"ok": False, "error": "Введите хост"}
        if not _valid_host(host):
            return {"ok": False, "error": "Недопустимый хост"}, 400
        try:
            r = core_process.run(
                ["tracepath", host],
                timeout=30
            )
            if r.returncode != 0:
                r = core_process.run(
                    ["traceroute", "-m", "15", "-w", "2", host],
                    timeout=30
                )
            return {"ok": True, "output": r.stdout or r.stderr}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/apps/nettools")
    @login_required
    def app_nettools():
        return render_template("apps/nettools.html", **page_data())

    @app.route("/api/wifi/scan")
    @login_required
    def api_wifi_scan():
        # перенос парсинга iw в core.network (PHASE 2.0-6), JSON-контракт прежний
        wifi_ifaces = _cfg("network", "wifi_ifaces", ["wlan1", "wlan0"])
        return core_net.wifi_scan(wifi_ifaces)

    @app.route("/api/network/info")
    @login_required
    def api_network_info():
        """Read-only объекты Core Network Manager (спека §5)."""
        interfaces = core_net.list_interfaces()
        addresses = core_net.list_addresses()
        routes = core_net.list_routes()
        if interfaces is None or addresses is None or routes is None:
            return {"ok": False,
                    "error": "не удалось прочитать состояние сети",
                    "interfaces": interfaces, "addresses": addresses,
                    "routes": routes}
        return {"ok": True, "interfaces": interfaces,
                "addresses": addresses, "routes": routes}

    @app.route("/api/network/address", methods=["POST"])
    @admin_required
    def api_network_address():
        """Транзакционная смена/удаление адреса (спека §6, admin)."""
        data = request.get_json(silent=True) or {}
        action = data.get("action")
        if action not in ("set", "del"):
            return {"ok": False, "error": "неизвестное действие"}, 400
        try:
            grace = int(data.get("insurance_grace", 20))
        except (TypeError, ValueError):
            grace = 20
        grace = max(0, min(300, grace))
        fn = core_net.set_address if action == "set" else core_net.del_address
        return fn(data.get("iface", ""), data.get("cidr", ""),
                  insurance_grace=grace)

    @app.route("/apps/wifianalyzer")
    @login_required
    def app_wifianalyzer():
        return render_template("apps/wifianalyzer.html", **page_data())

    @app.route("/api/bluetooth/status")
    @login_required
    def api_bluetooth_status():
        try:
            try:
                adapters = os.listdir("/sys/class/bluetooth")
            except Exception:
                adapters = []
            if not adapters:
                return {"ok": True, "adapter": None, "name": "Нет адаптера", "mac": "", "powered": False, "discovering": False, "error": "Bluetooth adapter not found"}
            out = _bt_cmd("show")
            info = {"adapter": adapters[0], "name": "", "mac": "", "powered": False, "discovering": False, "discoverable": False}
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Name:"):
                    info["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("Controller"):
                    parts = line.split()
                    if len(parts) >= 2:
                        info["mac"] = parts[1]
                elif line.startswith("Powered:"):
                    info["powered"] = "yes" in line.lower()
                elif line.startswith("Discovering:"):
                    info["discovering"] = "yes" in line.lower()
                elif line.startswith("Discoverable:") and "Timeout" not in line:
                    info["discoverable"] = "yes" in line.lower()
            return {"ok": True, **info}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/scan", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_scan():
        try:
            started = _spawn_bt_scan()
            return {"ok": True, "already_running": not started}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/discoverable", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_discoverable():
        on = request.json.get("on", True)
        cmd = "discoverable on" if on else "discoverable off"
        try:
            out = _bt_cmd(cmd)
            return {"ok": True, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/devices")
    @login_required
    def api_bluetooth_devices():
        try:
            out = _bt_cmd("devices")
            devices = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Device"):
                    parts = line.split(" ", 2)
                    if len(parts) >= 3:
                        mac = parts[1]
                        name = parts[2]
                        dev_type = "unknown"
                        if any(x in name.lower() for x in ["speaker", "headphone", "earbuds", "audio", "jbl", "sony", "bluetooth"]):
                            dev_type = "audio"
                        elif any(x in name.lower() for x in ["keyboard", "mouse"]):
                            dev_type = "input"
                        elif any(x in name.lower() for x in ["phone", "galaxy", "iphone"]):
                            dev_type = "phone"
                        connected = False
                        info_out = _bt_cmd("info %s" % mac)
                        for il in info_out.splitlines():
                            il = il.strip()
                            if il.startswith("Connected:") and "yes" in il.lower():
                                connected = True
                        devices.append({
                            "name": name, "mac": mac, "type": dev_type,
                            "paired": True, "connected": connected, "signal": 0
                        })
            return {"ok": True, "devices": devices}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/connect", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_connect():
        mac = request.json.get("mac", "")
        if not _valid_mac(mac):
            return {"ok": False, "error": "Неверный MAC"}, 400
        try:
            out = _bt_cmd("connect %s" % mac.strip(), timeout=15)
            ok = "successful" in out.lower() or "connection successful" in out.lower()
            return {"ok": ok, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/disconnect", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_disconnect():
        mac = request.json.get("mac", "")
        if not _valid_mac(mac):
            return {"ok": False, "error": "Неверный MAC"}, 400
        try:
            out = _bt_cmd("disconnect %s" % mac.strip())
            return {"ok": True, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/pair", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_pair():
        mac = request.json.get("mac", "")
        if not _valid_mac(mac):
            return {"ok": False, "error": "Неверный MAC"}, 400
        try:
            out = _bt_cmd("pair %s" % mac.strip(), timeout=30)
            ok = "successful" in out.lower()
            return {"ok": ok, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/remove", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_remove():
        mac = request.json.get("mac", "")
        if not _valid_mac(mac):
            return {"ok": False, "error": "Неверный MAC"}, 400
        try:
            out = _bt_cmd("remove %s" % mac.strip())
            return {"ok": True, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/api/bluetooth/power", methods=["POST"])
    @can_edit
    @login_required
    def api_bluetooth_power():
        on = request.json.get("on", True)
        cmd = "power on" if on else "power off"
        try:
            out = _bt_cmd(cmd)
            return {"ok": True, "output": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route("/apps/bluetooth")
    @login_required
    def app_bluetooth():
        return render_template("apps/bluetooth.html", **page_data())

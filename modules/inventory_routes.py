import json
import sqlite3
import threading

from flask import render_template, jsonify, redirect, url_for

from modules.inventory import get_inventory, scan_all_devices

_inv_scan_lock = threading.Lock()


def _spawn_inventory_scan():
    """Запустить inventory-скан, если предыдущий ещё идёт (P1-8 guard)."""
    if not _inv_scan_lock.acquire(blocking=False):
        return False

    def _run():
        try:
            scan_all_devices()
        finally:
            _inv_scan_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return True


def register_routes(app, ctx):
    login_required = ctx.login_required
    page_data = ctx.page_data
    _cfg = ctx._cfg
    DB = ctx.DB

    @app.route("/inventory")
    @login_required
    def inventory():
        data = page_data()

        try:
            scanned = get_inventory()
        except Exception:
            scanned = []

        scanned_dict = {}
        for inv in scanned:
            ip = inv.get("ip", "") if isinstance(inv, dict) else getattr(inv, "ip", "")
            scanned_dict[ip] = inv

        known_web_ports = _cfg("network", "known_web_ports", {
            "192.168.3.234": 8080, "192.168.3.235": 8080, "192.168.3.51": 8080,
        })

        all_devices = []
        try:
            con = sqlite3.connect(DB, timeout=30)
            con.execute("PRAGMA busy_timeout=30000")
            device_names, device_online, device_types = {}, {}, {}
            for row in con.execute("SELECT ip, name, online, device_type FROM devices"):
                device_names[row[0]] = row[1] or ""
                device_online[row[0]] = row[2]
                device_types[row[0]] = row[3] or ""

            for row in con.execute("SELECT ip, hostname, online FROM devices ORDER BY ip"):
                ip = row[0]
                if ip in scanned_dict:
                    inv = scanned_dict[ip]
                    if isinstance(inv, dict):
                        inv["device_name"] = device_names.get(ip, "")
                        inv["online"] = device_online.get(ip, 0)
                        inv["device_type"] = device_types.get(ip, "") or inv.get("device_type", "")
                        inv["has_web"] = False
                        inv["web_port"] = None
                        if inv.get("open_ports"):
                            try:
                                ports = json.loads(inv["open_ports"]) if isinstance(inv["open_ports"], str) else inv["open_ports"]
                                open_web = [
                                    p.get("port") for p in ports
                                    if p.get("state") == "open"
                                    and p.get("port") in (
                                        "80", "443", "8080", "8443", "8081",
                                        "8888", "9091", "8000", "3000", "5000",
                                    )
                                ]
                                if open_web:
                                    inv["has_web"] = True
                                    priority = (
                                        "80", "8080", "8000", "8888", "5000",
                                        "3000", "9091", "8081", "443", "8443",
                                    )
                                    inv["web_port"] = next(
                                        (x for x in priority if x in open_web),
                                        str(open_web[0]),
                                    )
                            except Exception:
                                pass
                        if not inv["has_web"] and ip in known_web_ports:
                            inv["has_web"] = True
                            inv["web_port"] = str(known_web_ports[ip])
                        if inv.get("open_ports"):
                            try:
                                raw = inv["open_ports"]
                                all_ports = json.loads(raw) if isinstance(raw, str) else raw
                                inv["open_ports"] = [p for p in all_ports if p.get("state") == "open"]
                            except Exception:
                                pass
                    all_devices.append(inv)
                else:
                    has_web = ip in known_web_ports
                    all_devices.append({
                        "ip": ip, "hostname": row[1] or "", "online": row[2],
                        "device_name": device_names.get(ip, ""), "has_web": has_web,
                        "web_port": str(known_web_ports[ip]) if has_web else None,
                        "open_ports": None, "device_type": device_types.get(ip, ""),
                        "model": None, "manufacturer": None, "last_scan": None, "not_scanned": True
                    })
            con.close()
        except Exception:
            pass

        _aliases = _cfg("network", "web_aliases", {"192.168.3.234": "192.168.3.235"})
        for inv in all_devices:
            inv["web_ip"] = _aliases.get(inv.get("ip", ""), inv.get("ip", ""))
            inv["web_scheme"] = (
                "https" if str(inv.get("web_port") or "") in ("443", "8443") else "http"
            )

        def sort_key(inv):
            online = int(inv.get("online", 0) if isinstance(inv, dict) else getattr(inv, "online", 0) or 0)
            has_web = inv.get("has_web", False) if isinstance(inv, dict) else getattr(inv, "has_web", False)
            open_ports = inv.get("open_ports") if isinstance(inv, dict) else getattr(inv, "open_ports", None)
            has_ports = False
            if open_ports:
                try:
                    ports = json.loads(open_ports) if isinstance(open_ports, str) else open_ports
                    has_ports = len(ports) > 0
                except Exception:
                    pass
            if online and has_web:
                return 0
            elif online and has_ports:
                return 1
            elif online:
                return 2
            else:
                return 3

        all_devices.sort(key=sort_key)
        data["inventories"] = all_devices

        return render_template("inventory.html", **data)

    @app.route("/inventory/scan", methods=["POST"])
    @login_required
    def inventory_scan():
        _spawn_inventory_scan()
        return redirect(url_for("inventory"))

    @app.route("/inventory/device/<ip>")
    @login_required
    def inventory_device(ip):
        # STEP 12: мёртвая заглушка (5 строк, без JS/ссылок) → device-detail.
        return redirect("/device/" + ip)

    @app.route("/api/inventory/<ip>")
    @login_required
    def api_inventory(ip):
        return jsonify(get_inventory(ip))

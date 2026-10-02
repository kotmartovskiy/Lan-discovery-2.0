import time

from flask import render_template, jsonify

_monitoring_cache = {"data": None, "ts": 0}


def register_routes(app, ctx):
    login_required = ctx.login_required
    page_data = ctx.page_data
    _cfg = ctx._cfg
    from modules.monitor import get_system_overview, get_netdata_stats_for_host

    @app.route("/monitoring")
    @login_required
    def monitoring():
        now = time.time()
        if _monitoring_cache["data"] is not None and now - _monitoring_cache["ts"] < 10:
            return render_template("monitoring.html", **_monitoring_cache["data"])

        data = page_data()

        # §30: без плато-специфики в дефолтах — хосты задаются в настройках
        hosts = _cfg("monitoring", "hosts", [])

        monitoring_hosts = []
        for host in hosts:
            info = {"ip": host["ip"], "name": host["name"], "netdata": False,
                    "cpu_percent": None, "ram_percent": None, "uptime": ""}
            if host.get("netdata"):
                try:
                    stats = get_netdata_stats_for_host(host["ip"])
                    info["netdata"] = True
                    info["cpu_percent"] = stats.get("cpu_percent", 0)
                    info["ram_percent"] = stats.get("ram_percent", 0)
                except Exception:
                    pass
            monitoring_hosts.append(info)

        try:
            netdata_overview = get_system_overview()
        except Exception:
            netdata_overview = None

        if netdata_overview and netdata_overview.get("ram"):
            ram = netdata_overview["ram"]
            netdata_overview["ram_items"] = [
                {"label": l, "value": v}
                for l, v in zip(ram.get("labels", []), ram.get("values", []))
            ]
        elif netdata_overview:
            netdata_overview["ram_items"] = []

        if netdata_overview and netdata_overview.get("network"):
            net = netdata_overview["network"]
            netdata_overview["net_items"] = [
                {"label": l, "value": v}
                for l, v in zip(net.get("labels", []), net.get("values", []))
            ]
        elif netdata_overview:
            netdata_overview["net_items"] = []

        data["monitoring_hosts"] = monitoring_hosts
        data["netdata_overview"] = netdata_overview

        _monitoring_cache["data"] = data
        _monitoring_cache["ts"] = time.time()

        return render_template("monitoring.html", **data)

    @app.route("/api/monitoring/<ip>")
    @login_required
    def api_monitoring(ip):
        from modules.monitor import (
            _get_cpu_percent_from_netdata,
            _get_ram_from_netdata,
        )

        if ip in ("127.0.0.1", "localhost") or \
                ip in (_cfg("network", "self_ips") or []):
            return jsonify(get_system_overview())

        cpu = _get_cpu_percent_from_netdata(ip)
        ram = _get_ram_from_netdata(ip)
        if cpu is None and ram is None:
            return jsonify({"error": "host unreachable"}), 503

        return jsonify({
            "cpu": {"busy_percent": cpu or 0},
            "ram": ram or {"percent": 0, "items": []},
            "ram_items": (ram or {}).get("items", []),
            "net_items": [],
            "temperature": 0,
        })

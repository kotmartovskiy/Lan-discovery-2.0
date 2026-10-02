import requests
import json
import re
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from core import process
from core.hardware import thermal_temp


NETDATA_URL = "http://127.0.0.1:19999"

_cpu_cache = {"percent": 0, "ts": 0}


def _cmd(cmd, timeout=10):
    return process.out(cmd, timeout=timeout)


def _get_cpu_percent_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.cpu&points=2&format=json&options=percentage",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            labels = data.get("labels", [])
            rows = data.get("data", [])
            if len(rows) >= 2:
                prev = rows[-2]
                curr = rows[-1]
                busy = 0
                for i, label in enumerate(labels):
                    if label != "time":
                        val = curr[i] if i < len(curr) else 0
                        prev_val = prev[i] if i < len(prev) else 0
                        diff = (val or 0) - (prev_val or 0)
                        if diff > 0:
                            busy += diff
                return round(busy, 1)
            elif len(rows) == 1:
                curr = rows[0]
                busy = 0
                for i, label in enumerate(labels):
                    if label != "time":
                        val = curr[i] if i < len(curr) else 0
                        if val and val > 0:
                            busy += val
                return round(busy, 1)
    except Exception:
        pass
    return None


def _get_ram_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.ram&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            labels = data.get("labels", [])
            values = data.get("data", [[]])[0] if data.get("data") else []
            used = 0
            total = 0
            for lbl, val in zip(labels, values):
                if lbl == "time":
                    continue
                total += val or 0
                if "used" in lbl.lower() and "cache" not in lbl.lower():
                    used = val or 0
            percent = round(used / total * 100, 1) if total > 0 else 0
            items = []
            for lbl, val in zip(labels, values):
                if lbl != "time":
                    items.append({"label": lbl, "value": round(val or 0, 1)})
            return {"percent": percent, "items": items}
    except Exception:
        pass
    return None


def _get_disk_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/charts",
            timeout=3
        )
        if resp.ok:
            charts = resp.json().get("charts", {})
            disk_charts = []
            for name, info in charts.items():
                if "disk" in name.lower() and "io" not in name.lower():
                    disk_charts.append(name)

            if disk_charts:
                resp2 = requests.get(
                    f"http://{host_ip}:{port}/api/v1/data"
                    f"?chart={disk_charts[0]}&points=1&format=json",
                    timeout=3
                )
                if resp2.ok:
                    data = resp2.json()
                    labels = data.get("labels", [])
                    values = data.get("data", [[]])[0] if data.get("data") else []
                    items = []
                    for lbl, val in zip(labels, values):
                        if lbl != "time":
                            items.append({"label": lbl, "value": round(val or 0, 1)})
                    return items
    except Exception:
        pass
    return []


def _get_net_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.net&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            labels = data.get("labels", [])
            values = data.get("data", [[]])[0] if data.get("data") else []
            items = []
            for lbl, val in zip(labels, values):
                if lbl != "time":
                    items.append({"label": lbl, "value": round(val or 0, 1)})
            return items
    except Exception:
        pass
    return []


def _get_cpu_detail_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.cpu&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            labels = data.get("labels", [])
            values = data.get("data", [[]])[0] if data.get("data") else []
            items = []
            for lbl, val in zip(labels, values):
                if lbl != "time":
                    items.append({"label": lbl, "value": round(val or 0, 1)})
            return items
    except Exception:
        pass
    return []


def _get_uptime_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.uptime&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            values = data.get("data", [[]])[0] if data.get("data") else []
            if values and len(values) > 0:
                seconds = values[0] or 0
                days = int(seconds // 86400)
                hours = int((seconds % 86400) // 3600)
                mins = int((seconds % 3600) // 60)
                return f"{days}d {hours}h {mins}m"
    except Exception:
        pass
    return ""


def _get_load_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=system.load&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            labels = data.get("labels", [])
            values = data.get("data", [[]])[0] if data.get("data") else []
            items = []
            for lbl, val in zip(labels, values):
                if lbl != "time":
                    items.append({"label": lbl, "value": round(val or 0, 2)})
            return items
    except Exception:
        pass
    return []


def _get_processes_from_netdata(host_ip, port=19999):
    try:
        resp = requests.get(
            f"http://{host_ip}:{port}/api/v1/data"
            f"?chart=processes.running&points=1&format=json",
            timeout=3
        )
        if resp.ok:
            data = resp.json()
            values = data.get("data", [[]])[0] if data.get("data") else []
            if values and len(values) > 0:
                return int(values[0] or 0)
    except Exception:
        pass
    return 0


def get_netdata_stats_for_host(host_ip):
    stats = {
        "cpu_percent": None,
        "ram_percent": None,
        "disk_io": 0,
        "net_rx": 0,
        "net_tx": 0,
        "temperature": 0,
        "uptime": "",
        "load_avg": [0, 0, 0],
        "netdata": False,
        "cpu_detail": [],
        "ram_detail": [],
        "disk_detail": [],
        "net_detail": [],
        "load_detail": [],
        "processes": 0,
    }

    netdata_host_url = f"http://{host_ip}:19999"

    try:
        resp = requests.get(
            f"{netdata_host_url}/api/v1/info",
            timeout=2
        )
        if resp.ok:
            stats["netdata"] = True
    except Exception:
        stats["netdata"] = False

    if not stats["netdata"]:
        return stats

    def _fetch(func, *args):
        try:
            return func(*args)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_cpu = pool.submit(_fetch, _get_cpu_percent_from_netdata, host_ip)
        f_ram = pool.submit(_fetch, _get_ram_from_netdata, host_ip)
        f_cpu_detail = pool.submit(_fetch, _get_cpu_detail_from_netdata, host_ip)
        f_disk = pool.submit(_fetch, _get_disk_from_netdata, host_ip)
        f_net = pool.submit(_fetch, _get_net_from_netdata, host_ip)
        f_load = pool.submit(_fetch, _get_load_from_netdata, host_ip)
        f_uptime = pool.submit(_fetch, _get_uptime_from_netdata, host_ip)
        f_procs = pool.submit(_fetch, _get_processes_from_netdata, host_ip)

        cpu = f_cpu.result()
        if cpu is not None:
            stats["cpu_percent"] = cpu

        ram = f_ram.result()
        if ram:
            stats["ram_percent"] = ram["percent"]
            stats["ram_detail"] = ram["items"]

        stats["cpu_detail"] = f_cpu_detail.result() or []
        stats["disk_detail"] = f_disk.result() or []
        stats["net_detail"] = f_net.result() or []
        stats["load_detail"] = f_load.result() or []
        stats["uptime"] = f_uptime.result() or ""
        stats["processes"] = f_procs.result() or 0

    return stats


def _read_cpu_stat():
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline().split()
        if line[0] == "cpu":
            return [int(x) for x in line[1:]]
    except Exception:
        pass
    return None


def _calc_cpu_percent():
    global _cpu_cache
    now = time.time()
    if now - _cpu_cache["ts"] < 3:
        return _cpu_cache["percent"]

    vals2 = _read_cpu_stat()
    if vals2 is None:
        return _cpu_cache["percent"]

    prev = _cpu_cache.get("prev_vals")
    _cpu_cache["prev_vals"] = vals2
    _cpu_cache["ts"] = now

    if prev is None or len(prev) != len(vals2):
        return _cpu_cache["percent"]

    total_diff = sum(a - b for a, b in zip(vals2, prev))
    idle_diff = vals2[3] - prev[3]
    if total_diff > 0:
        _cpu_cache["percent"] = round((1 - idle_diff / total_diff) * 100, 1)

    return _cpu_cache["percent"]


def get_system_overview():
    cpu_percent = _calc_cpu_percent()

    ram_items = []
    ram_percent = 0
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    meminfo[k] = int(v.strip().split()[0])
        total = meminfo.get("MemTotal", 1)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        ram_percent = round(used / total * 100, 1)
        ram_items = [
            {"label": "total", "value": round(total / 1024, 1)},
            {"label": "used", "value": round(used / 1024, 1)},
            {"label": "available", "value": round(available / 1024, 1)},
        ]
    except Exception:
        pass

    net_items = []
    try:
        from app import _cfg
        traffic_ifaces = _cfg("network", "traffic_ifaces",
                              ["end0", "eth0", "wlan1", "wlan0"])
        for iface in traffic_ifaces:
            try:
                with open(f"/sys/class/net/{iface}/statistics/rx_bytes") as f:
                    rx = int(f.read().strip())
                with open(f"/sys/class/net/{iface}/statistics/tx_bytes") as f:
                    tx = int(f.read().strip())
                net_items.append({"label": f"{iface} RX", "value": round(rx / 1024 / 1024, 1)})
                net_items.append({"label": f"{iface} TX", "value": round(tx / 1024 / 1024, 1)})
            except Exception:
                pass
    except Exception:
        pass

    temp = thermal_temp() or 0

    return {
        "cpu": {"busy_percent": cpu_percent, "labels": [], "values": []},
        "ram": {"percent": ram_percent, "items": ram_items},
        "ram_items": ram_items,
        "net_items": net_items,
        "network": {"labels": [i["label"] for i in net_items], "values": [i["value"] for i in net_items]},
        "temperature": temp,
    }


def scan_device_full(ip):
    from modules.inventory import scan_device_full as _scan
    return _scan(ip)


def get_inventory(ip=None):
    from modules.inventory import get_inventory as _get
    return _get(ip)


def scan_all_devices():
    from modules.inventory import scan_all_devices as _scan
    return _scan()

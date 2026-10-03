import subprocess
import json
import re
import socket
import sqlite3
from datetime import datetime


# Task4: БД — из core.db (единый источник пути, §20/§25)
from core.db import DB


def _cmd(cmd, timeout=10):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _read_file(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def init_inventory_db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")

    con.execute("""
        CREATE TABLE IF NOT EXISTS device_inventory (
            ip TEXT PRIMARY KEY,
            hostname TEXT,
            mac TEXT,
            device_type TEXT,
            manufacturer TEXT,
            model TEXT,
            serial_number TEXT,
            firmware TEXT,
            os_info TEXT,
            cpu_model TEXT,
            cpu_cores INTEGER,
            ram_total TEXT,
            disk_info TEXT,
            open_ports TEXT,
            sensors TEXT,
            netdata_host INTEGER DEFAULT 0,
            last_scan TEXT,
            raw_data TEXT
        )
    """)

    con.commit()
    con.close()


def nmap_os_detection(ip):
    result = {
        "os": "",
        "ports": [],
        "device_type": "",
    }

    try:
        output = _cmd(
            ["nmap", "-O", "-sV", "--top-ports", "20", ip],
            timeout=30
        )

        os_match = re.search(r"Running: (.+)", output)
        if os_match:
            result["os"] = os_match.group(1).strip()

        os_details = re.search(r"OS details?: (.+)", output)
        if os_details:
            result["os"] = os_details.group(1).strip()

        dev_match = re.search(r"Device type: (.+)", output)
        if dev_match:
            result["device_type"] = dev_match.group(1).strip()

        for line in output.splitlines():
            port_match = re.match(
                r"(\d+)/(tcp|udp)\s+(\w+)\s+(.+)",
                line
            )
            if port_match:
                result["ports"].append({
                    "port": port_match.group(1),
                    "proto": port_match.group(2),
                    "state": port_match.group(3),
                    "service": port_match.group(4).strip()
                })

    except Exception:
        pass

    return result


def snmp_get(ip, community="public", oid=".1"):
    try:
        output = _cmd(
            [
                "snmpwalk",
                "-v2c",
                "-c", community,
                "-t", "3",
                ip,
                oid
            ],
            timeout=8
        )
        return output
    except Exception:
        return ""


def snmp_inventory(ip):
    result = {
        "manufacturer": "",
        "model": "",
        "serial": "",
        "firmware": "",
        "uptime": "",
    }

    try:
        sysDescr = snmp_get(ip, oid="1.3.6.1.2.1.1.1.0")
        if sysDescr:
            result["firmware"] = sysDescr.split(":", 1)[-1].strip() \
                if ":" in sysDescr else sysDescr

        sysName = snmp_get(ip, oid="1.3.6.1.2.1.1.5.0")
        if sysName:
            result["model"] = sysName

        sysUpTime = snmp_get(ip, oid="1.3.6.1.2.1.1.3.0")
        if sysUpTime:
            result["uptime"] = sysUpTime

        sysObjectID = snmp_get(ip, oid="1.3.6.1.2.1.1.2.0")
        if sysObjectID:
            if "huawei" in sysObjectID.lower():
                result["manufacturer"] = "Huawei"
            elif "cisco" in sysObjectID.lower():
                result["manufacturer"] = "Cisco"

    except Exception:
        pass

    return result


def mdns_discover(ip):
    result = {
        "services": [],
        "model": "",
        "firmware": "",
    }

    try:
        output = _cmd(
            ["nmap", "-sU", "-p", "5353", "--script", "mdns-info", ip],
            timeout=15
        )

        for line in output.splitlines():
            if "mdns-info" in line.lower():
                result["services"].append(line.strip())

    except Exception:
        pass

    return result


def upnp_discover(ip):
    result = {
        "friendly_name": "",
        "manufacturer": "",
        "model": "",
        "firmware": "",
        "serial": "",
    }

    try:
        output = _cmd(
            ["nmap", "-sV", "-p", "1900", "--script", "upnp-info", ip],
            timeout=15
        )

        for line in output.splitlines():
            line = line.strip()
            if "Friendly Name:" in line:
                result["friendly_name"] = line.split(":", 1)[-1].strip()
            elif "Manufacturer:" in line:
                result["manufacturer"] = line.split(":", 1)[-1].strip()
            elif "Model Name:" in line:
                result["model"] = line.split(":", 1)[-1].strip()
            elif "Firmware:" in line or "Software:" in line:
                result["firmware"] = line.split(":", 1)[-1].strip()

    except Exception:
        pass

    return result


def scan_device_full(ip):
    init_inventory_db()

    inventory = {
        "ip": ip,
        "hostname": "",
        "mac": "",
        "device_type": "",
        "manufacturer": "",
        "model": "",
        "serial": "",
        "firmware": "",
        "os_info": "",
        "cpu_model": "",
        "cpu_cores": 0,
        "ram_total": "",
        "disk_info": "",
        "open_ports": [],
        "sensors": {},
        "netdata_host": False,
        "last_scan": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        inv = nmap_os_detection(ip)
        inventory["os_info"] = inv["os"]
        inventory["device_type"] = inv["device_type"]
        inventory["open_ports"] = inv["ports"]
    except Exception:
        pass

    try:
        snmp = snmp_inventory(ip)
        if snmp.get("manufacturer"):
            inventory["manufacturer"] = snmp["manufacturer"]
        if snmp.get("model"):
            inventory["model"] = snmp["model"]
        if snmp.get("serial"):
            inventory["serial"] = snmp["serial"]
        if snmp.get("firmware"):
            inventory["firmware"] = snmp["firmware"]
    except Exception:
        pass

    try:
        upnp = upnp_discover(ip)
        if upnp.get("friendly_name") and not inventory["model"]:
            inventory["model"] = upnp["friendly_name"]
        if upnp.get("manufacturer") and not inventory["manufacturer"]:
            inventory["manufacturer"] = upnp["manufacturer"]
        if upnp.get("firmware") and not inventory["firmware"]:
            inventory["firmware"] = upnp["firmware"]
        if upnp.get("serial") and not inventory["serial"]:
            inventory["serial"] = upnp["serial"]
    except Exception:
        pass

    try:
        resp_code = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{ip}:19999/api/v1/info"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if resp_code.stdout.strip() == "200":
            inventory["netdata_host"] = True
    except Exception:
        pass

    if inventory["netdata_host"]:
        try:
            resp = subprocess.run(
                [
                    "curl", "-s",
                    f"http://{ip}:19999/api/v1/data"
                    f"?chart=system.cpu&points=1&format=json"
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            if resp.stdout:
                data = json.loads(resp.stdout)
                labels = data.get("labels", [])
                values = data.get("data", [[]])[0] if data.get("data") else []
                for lbl, val in zip(labels, values):
                    if "model" in lbl.lower():
                        inventory["cpu_model"] = str(val)
        except Exception:
            pass

    try:
        con = sqlite3.connect(DB, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")

        con.execute("""
            INSERT OR REPLACE INTO device_inventory
            (ip, hostname, mac, device_type, manufacturer, model,
             serial_number, firmware, os_info, cpu_model, cpu_cores,
             ram_total, disk_info, open_ports, sensors, netdata_host,
             last_scan, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            inventory["ip"],
            inventory["hostname"],
            inventory["mac"],
            inventory["device_type"],
            inventory["manufacturer"],
            inventory["model"],
            inventory["serial"],
            inventory["firmware"],
            inventory["os_info"],
            inventory["cpu_model"],
            inventory["cpu_cores"],
            inventory["ram_total"],
            inventory["disk_info"],
            json.dumps(inventory["open_ports"], ensure_ascii=False),
            json.dumps(inventory["sensors"], ensure_ascii=False),
            1 if inventory["netdata_host"] else 0,
            inventory["last_scan"],
            json.dumps(inventory, ensure_ascii=False)
        ))

        con.commit()
        con.close()

    except Exception as e:
        print(f"INVENTORY DB ERROR: {e}", flush=True)

    return inventory


def get_inventory(ip=None):
    init_inventory_db()

    con = sqlite3.connect(DB, timeout=10)

    if ip:
        row = con.execute(
            "SELECT * FROM device_inventory WHERE ip = ?",
            (ip,)
        ).fetchone()
        con.close()
        if row:
            return _row_to_dict(row)
        return None
    else:
        rows = con.execute(
            "SELECT * FROM device_inventory ORDER BY ip"
        ).fetchall()
        con.close()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row):
    columns = [
        "ip", "hostname", "mac", "device_type", "manufacturer",
        "model", "serial_number", "firmware", "os_info",
        "cpu_model", "cpu_cores", "ram_total", "disk_info",
        "open_ports", "sensors", "netdata_host", "last_scan", "raw_data"
    ]
    result = {}
    for i, col in enumerate(columns):
        val = row[i] if i < len(row) else None
        if col in ("open_ports", "sensors", "raw_data") and val:
            try:
                result[col] = json.loads(val)
            except Exception:
                result[col] = val
        else:
            result[col] = val
    return result


def scan_all_devices():
    init_inventory_db()

    from core.config import get as _cfg
    subnet = _cfg("network", "subnet", "192.168.3.0/24")

    try:
        result = subprocess.run(
            ["nmap", "-sn", subnet],
            capture_output=True,
            text=True,
            timeout=60
        )

        hosts = []
        for line in result.stdout.splitlines():
            match = re.search(
                r"Nmap scan report for (?:(.+?) \()?([\d.]+)\)?",
                line
            )
            if match:
                hostname = match.group(1) or ""
                ip = match.group(2)
                hosts.append(ip)

        for ip in hosts:
            print(f"INVENTORY SCAN: {ip}", flush=True)
            scan_device_full(ip)

        print(f"INVENTORY COMPLETE: {len(hosts)} devices", flush=True)
        return len(hosts)

    except Exception as e:
        print(f"INVENTORY ERROR: {e}", flush=True)
        return 0

# -*- coding: utf-8 -*-
"""Спасательный AP — ядро модуля rescue.

Поднимает Wi-Fi точку доступа (hostapd + dnsmasq) на свободном
wlan-интерфейсе, чтобы при падении основной сети (роутер/провайдер)
доступ к панели сохранялся: подключаетесь к SSID «LAN-Rescue» и
заходите на 192.168.4.1:8080.

Сценарий:
- интерфейс с default route НИКОГДА не трогается (если wlan0 — сам
  аплинк → AP там невозможен без потери сети);
- конфиг: SSID/WPA2/канал/подсеть (по умолчанию 192.168.4.1/24);
- start: пишет hostapd.conf + dnsmasq.conf в /etc/lan-discovery/rescue/,
  назначает IP интерфейсу, запускает dnsmasq + hostapd (процессы,
  не systemd — чтобы не конфликтовать с NetworkManager);
- stop: останавливает процессы, снимает IP;
- hostapd/dnsmasq ставятся кнопкой (admin, whitelist apt).

ВНИМАНИЕ: AP живёт только пока включён бокс; это спасательный, а не
постоянный режим.
"""
import glob
import json
import os
import re
import signal
import subprocess
import threading
import time

from core import config, process

CONF_DIR = "/etc/lan-discovery/rescue"
CONF_PATH = "/etc/lan-discovery/rescue.json"
HOSTAPD_CONF = os.path.join(CONF_DIR, "hostapd.conf")
DNSMASQ_CONF = os.path.join(CONF_DIR, "dnsmasq.conf")
PID_HOSTAPD = os.path.join(CONF_DIR, "hostapd.pid")
PID_DNSMASQ = os.path.join(CONF_DIR, "dnsmasq.pid")

_IFACE_RE = re.compile(r"^wlan\d+$")
_SSID_RE = re.compile(r"^[\x20-\x7e]{1,32}$")  # printable, ≤32
_PASS_RE = re.compile(r"^[\x20-\x7e]{8,63}$")

DEFAULTS = {
    "ssid": "LAN-Rescue",
    "passphrase": "",       # пусто = открытая сеть (для быстрого спасения)
    "channel": 6,
    "subnet": "192.168.4.1/24",
    "dhcp_start": "192.168.4.10",
    "dhcp_end": "192.168.4.100",
    "iface": "",
}

APT_ALLOWED = {"hostapd", "dnsmasq"}

_install = {"running": False, "pkg": "", "log": []}
_install_lock = threading.Lock()


# ---------------------------------------------------------------- detect

def _which(b):
    return bool(process.out(["which", b], timeout=3).strip())


def tools():
    return {"hostapd": _which("hostapd"), "dnsmasq": _which("dnsmasq")}


def _default_ifaces():
    """wlan-интерфейсы из /sys/class/net (без iw)."""
    out = []
    for path in sorted(glob.glob("/sys/class/net/wlan*")):
        name = os.path.basename(path)
        if _IFACE_RE.match(name):
            out.append(name)
    return out


def uplink_iface():
    """Интерфейс default route (его нельзя трогать для AP)."""
    out = process.out(["ip", "route", "show", "default"], timeout=3)
    m = re.search(r"\bdev\s+(\S+)", out)
    return m.group(1) if m else ""


def ifaces():
    """[{name, uplink, has_ip, ip}] — wlan-интерфейсы для выбора AP."""
    up = uplink_iface()
    out = []
    for name in _default_ifaces():
        ip = process.out(["ip", "-4", "addr", "show", "dev", name],
                         timeout=3)
        m = re.search(r"\binet\s+(\S+)", ip)
        out.append({"name": name, "uplink": name == up,
                    "has_ip": bool(m), "ip": m.group(1) if m else ""})
    return out


# ---------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved:
                cfg[k] = saved[k]
    except Exception:
        pass
    return cfg


def save_config(**kw):
    cfg = load_config()
    ssid = str(kw.get("ssid", cfg["ssid"]))
    if not _SSID_RE.match(ssid):
        raise ValueError("SSID: 1–32 печатных символов")
    passphrase = str(kw.get("passphrase", cfg["passphrase"]))
    if passphrase and not _PASS_RE.match(passphrase):
        raise ValueError("пароль: 8–63 печатных символов (или пусто = открытая)")
    channel = int(kw.get("channel", cfg["channel"]))
    if not 1 <= channel <= 16:
        raise ValueError("канал 1..16")
    subnet = str(kw.get("subnet", cfg["subnet"]))
    if not re.match(r"^\d+\.\d+\.\d+\.\d+/24$", subnet):
        raise ValueError("подсеть вида 192.168.4.1/24")
    iface = str(kw.get("iface", cfg["iface"]))
    if iface and not _IFACE_RE.match(iface):
        raise ValueError("интерфейс вида wlan0")
    cfg.update(ssid=ssid, passphrase=passphrase, channel=channel,
               subnet=subnet, iface=iface)
    config.write_json_atomic(CONF_PATH, cfg)
    return cfg


# --------------------------------------------------------------- process

def _read_pid(path):
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_pid(pid, path):
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass


def running():
    return {"hostapd": _alive(_read_pid(PID_HOSTAPD)),
            "dnsmasq": _alive(_read_pid(PID_DNSMASQ))}


def _iface_ip(iface):
    out = process.out(["ip", "-4", "addr", "show", "dev", iface], timeout=3)
    m = re.search(r"\binet\s+(\S+)", out)
    return m.group(1) if m else ""


def status():
    cfg = load_config()
    run = running()
    active = run["hostapd"] or run["dnsmasq"]
    return {
        "tools": tools(),
        "ifaces": ifaces(),
        "uplink": uplink_iface(),
        "config": {k: v for k, v in cfg.items() if k != "passphrase"},
        "has_pass": bool(cfg["passphrase"]),
        "running": active,
        "hostapd": run["hostapd"],
        "dnsmasq": run["dnsmasq"],
        "iface_ip": _iface_ip(cfg["iface"]) if cfg["iface"] and active else "",
    }


# ---------------------------------------------------------------- start

def _log_tail(path, lines=3):
    try:
        with open(path, "rb") as f:
            data = f.read(4096).decode("utf-8", "replace")
        return "\n".join(data.strip().splitlines()[-lines:])
    except Exception:
        return ""


def _write_confs(cfg):
    os.makedirs(CONF_DIR, exist_ok=True)
    secured = bool(cfg["passphrase"])
    hostapd = "\n".join([
        "interface=%s" % cfg["iface"],
        "driver=nl80211",
        "ssid=%s" % cfg["ssid"],
        "hw_mode=g",
        "channel=%d" % cfg["channel"],
        "wmm_enabled=0",
        "macaddr_acl=0",
        "auth_algs=1",
        "ignore_broadcast_ssid=0" if not secured else "wpa=2",
        "wpa_key_mgmt=WPA-PSK" if secured else "",
        "rsn_pairwise=CCMP" if secured else "",
        "wpa_passphrase=%s" % cfg["passphrase"] if secured else "",
    ]) + "\n"
    with open(HOSTAPD_CONF, "w") as f:
        f.write(hostapd)
    subnet_ip = cfg["subnet"].split("/", 1)[0]
    dnsmasq = "\n".join([
        "interface=%s" % cfg["iface"],
        "bind-interfaces",
        "dhcp-range=%s,%s,12h" % (cfg["dhcp_start"], cfg["dhcp_end"]),
        "address=/#/%s" % subnet_ip,
        "domain-needed",
        "bogus-priv",
    ]) + "\n"
    with open(DNSMASQ_CONF, "w") as f:
        f.write(dnsmasq)


def start(iface=None):
    """Поднять спасательный AP. Блокируется, если iface = uplink."""
    cfg = load_config()
    if iface:
        if not _IFACE_RE.match(iface):
            raise ValueError("интерфейс вида wlan0")
        if iface not in [it["name"] for it in ifaces()]:
            raise RuntimeError("интерфейс %s не найден на этом боксе"
                               % iface)
        cfg["iface"] = iface
    elif not cfg["iface"]:
        # первый свободный не-uplink wlan
        for it in ifaces():
            if not it["uplink"]:
                cfg["iface"] = it["name"]
                break
    if not cfg["iface"]:
        raise RuntimeError("нет свободного wlan-интерфейса для AP")
    if cfg["iface"] == uplink_iface():
        raise RuntimeError(
            "%s — интерфейс основной сети (default route); AP на нём "
            "оборвёт связь. Выберите другой wlan." % cfg["iface"])
    t = tools()
    if not t["hostapd"] or not t["dnsmasq"]:
        raise RuntimeError("hostapd/dnsmasq не установлены — поставьте "
                           "кнопкой на странице")
    _write_confs(cfg)

    # системный dnsmasq мог быть запущен apt-постом — мешает (порт 53)
    subprocess.run(["systemctl", "stop", "dnsmasq"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=15)

    # IP интерфейсу
    subnet = cfg["subnet"]
    process.run(["ip", "addr", "flush", "dev", cfg["iface"]], timeout=5)
    r = process.run(["ip", "addr", "add", subnet, "dev", cfg["iface"]],
                    timeout=5)
    if r.returncode != 0 and "File exists" not in (r.stderr or ""):
        raise RuntimeError("не удалось назначить IP %s: %s"
                           % (subnet, (r.stderr or "").strip()))
    r = process.run(["ip", "link", "set", cfg["iface"], "up"], timeout=5)
    if r.returncode != 0:
        raise RuntimeError("не удалось поднять %s: %s"
                           % (cfg["iface"], (r.stderr or "").strip()))

    # dnsmasq (daemonize — контролируем по pid-файлу, лог stderr в файл)
    dlog = open(os.path.join(CONF_DIR, "dnsmasq.log"), "ab")
    subprocess.Popen(["dnsmasq", "-C", DNSMASQ_CONF,
                      "--pid-file=" + PID_DNSMASQ],
                     stdout=subprocess.DEVNULL, stderr=dlog)
    for _ in range(30):
        if _alive(_read_pid(PID_DNSMASQ)):
            break
        time.sleep(0.1)
    else:
        tail = _log_tail(os.path.join(CONF_DIR, "dnsmasq.log"), 3)
        raise RuntimeError("dnsmasq не стартовал: %s" % (tail or "нет лога"))
    # hostapd (тоже daemonize, -P пишет pid-файл)
    proc2 = subprocess.Popen(["hostapd", "-B", HOSTAPD_CONF,
                              "-P", PID_HOSTAPD],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    out, err = proc2.communicate(timeout=10)
    if proc2.returncode != 0:
        _stop_pid(_read_pid(PID_DNSMASQ), PID_DNSMASQ)
        raise RuntimeError("hostapd не стартовал: %s"
                           % ((err or out or "").strip()[:300]))
    save_config(iface=cfg["iface"])  # сохранили только после успеха
    return status()


def stop():
    """Остановить AP и снять IP."""
    cfg = load_config()
    _stop_pid(_read_pid(PID_HOSTAPD), PID_HOSTAPD)
    _stop_pid(_read_pid(PID_DNSMASQ), PID_DNSMASQ)
    if cfg["iface"]:
        process.run(["ip", "addr", "flush", "dev", cfg["iface"]], timeout=5)
    return status()


# --------------------------------------------------------------- install

def install_status():
    return dict(_install)


def install(pkg):
    pkg = (pkg or "").strip()
    if pkg not in APT_ALLOWED:
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

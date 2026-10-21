# -*- coding: utf-8 -*-
"""Диагностика «Что сломано?» — ядро модуля diagnose.

Пошаговая проверка сети одним прогоном:
  lan      — адрес/шлюз/DNS хоста, источник адреса (DHCP/статик);
  gateway  — ping шлюза (роутера);
  dns      — резолв внешних имён;
  internet — HTTP-пробы (generate_204 / ya.ru / google.com);
  wan      — ping 8.8.8.8: потери и задержки до интернета;
  speed    — download-тест с зеркал (Mbps), при недоступности — warn.

Один прогон за раз (singleton), прогресс — status(); шаги публикуются
по мере выполнения — клиент видит текущий шаг live через
/api/diagnose/status. Результаты нигде не сохраняются (в отличие от
portscan) — это мгновенный снимок, не архив.
"""
import os
import re
import socket
import threading
import time
from datetime import datetime

from core import process

STEPS = (
    {"id": "lan", "title": "Локальная сеть (адрес, шлюз, DNS)"},
    {"id": "gateway", "title": "Шлюз роутера (ping)"},
    {"id": "dns", "title": "DNS (резолв внешних имён)"},
    {"id": "internet", "title": "Интернет (HTTP-пробы)"},
    {"id": "wan", "title": "Задержки и потери в интернете (ping WAN)"},
    {"id": "speed", "title": "Скорость канала (download-тест)"},
)

DNS_PROBES = ("ya.ru", "google.com", "cloudflare.com")
HTTP_PROBES = (
    ("http://connectivitycheck.gstatic.com/generate_204", (204,)),
    ("https://www.google.com/generate_204", (204,)),
    ("https://ya.ru", (200, 204, 301, 302)),
)
WAN_PING = "8.8.8.8"

# Тестовые зеркала download-теста (первое доступное). Обрезается по
# --max-time: меряется реальная скорость, а не размер файла.
SPEED_MIRRORS = (
    "http://speedtest.tele2.ru/1000mb.bin",
    "http://cachefly.cachefly.net/50mb.test",
    "http://ipv4.download.thinkbroadband.com/50MB.zip",
)

_IFACE_RE = re.compile(r"^\d+:\s*(\S+)\s+inet\s+(\S+)\s")
_GW_RE = re.compile(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)\s+dev\s+(\S+)")
_RESOLV_RE = re.compile(r"^nameserver\s+(\S+)\s*$", re.MULTILINE)
_PING_LOSS_RE = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+received.*?"
    r"(\d+(?:\.\d+)?)%\s+packet loss")
_PING_RTT_RE = re.compile(
    r"=\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)"
    r"(?:/(\d+(?:\.\d+)?))?\s+ms")


# ------------------------------------------------------------- парсинг/замер

def parse_gateway(route_out):
    """'default via X dev Y ...' -> (ip, dev) | (None, None)."""
    m = _GW_RE.search(route_out or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def parse_iface_line(line):
    """Строка 'ip -4 -o addr show scope global' -> (iface, cidr) | None."""
    m = _IFACE_RE.match(line or "")
    return (m.group(1), m.group(2)) if m else None


def parse_resolv(text):
    """nameserver-строки resolv.conf -> list[str]."""
    return _RESOLV_RE.findall(text or "")


def parse_ping(out):
    """Вывод Linux ping -> {sent, recvd, loss_pct, min, avg, max}."""
    st = {"sent": 0, "recvd": 0, "loss_pct": 100.0,
          "min": None, "avg": None, "max": None}
    m = _PING_LOSS_RE.search(out or "")
    if m:
        st["sent"] = int(m.group(1))
        st["recvd"] = int(m.group(2))
        st["loss_pct"] = float(m.group(3))
    m = _PING_RTT_RE.search(out or "")
    if m:
        st["min"], st["avg"], st["max"] = (float(m.group(i)) for i in (1, 2, 3))
    return st


def fmt_speed(bytes_per_sec):
    """Байт/с -> '12.3 Мбит/с'."""
    mbps = bytes_per_sec * 8 / 1_000_000
    if mbps >= 100:
        return "%.0f Мбит/с" % mbps
    return "%.1f Мбит/с" % mbps


def default_gateway():
    """(ip, dev) шлюза по умолчанию (ip route) | (None, None)."""
    out = process.out(["ip", "-4", "route", "show", "default"], timeout=3)
    return parse_gateway(out)


def lan_info():
    """{iface, cidr, ip, gw, gw_dev, dns[], dhcp} — конфигурация хоста."""
    addrs = []
    try:
        out = process.out(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                          timeout=3)
        for line in out.splitlines():
            parsed = parse_iface_line(line)
            if parsed:
                addrs.append(parsed)
    except Exception:
        pass
    gw, gw_dev = default_gateway()
    dns = []
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8",
                  errors="replace") as f:
            dns = parse_resolv(f.read())
    except Exception:
        pass
    return {
        "iface": addrs[0][0] if addrs else "",
        "cidr": addrs[0][1] if addrs else "",
        "ip": addrs[0][1].split("/", 1)[0] if addrs else "",
        "gw": gw or "",
        "gw_dev": gw_dev or "",
        "dns": dns,
        "dhcp": _dhcp_source(),
    }


def _dhcp_source():
    """Подсказка об источнике адреса: lease-файлы DHCP-клиентов."""
    lease_paths = (
        "/var/lib/dhcp/dhclient.leases",
        "/var/lib/dhcpcd/",
        "/run/systemd/netif/leases/",
        "/var/lib/NetworkManager/",
    )
    for p in lease_paths:
        try:
            if os.path.isdir(p):
                if os.listdir(p):
                    return "DHCP (lease-файлы: %s)" % p
            elif os.path.isfile(p):
                return "DHCP (lease: %s)" % p
        except Exception:
            continue
    return "статический/неизвестно"


def ping_stats(host, count=3, timeout=2):
    """ping -c count -W timeout -> parse_ping()."""
    try:
        out = process.out(["ping", "-c", str(count), "-W", str(timeout), host],
                          timeout=timeout * count + 3)
        return parse_ping(out)
    except Exception:
        return parse_ping("")


def dns_lookup(host, timeout=3):
    """Резолв хоста -> (ok, ms)."""
    try:
        t0 = time.time()
        socket.getaddrinfo(host, None, socket.AF_INET)
        return True, int((time.time() - t0) * 1000)
    except Exception:
        return False, None


def http_probe(url, timeout=4):
    """HTTP-проба curl -> (ok, http_code, time_ms)."""
    try:
        r = process.run(
            ["curl", "-s", "-o", "/dev/null", "-k", "-L",
             "--connect-timeout", str(timeout), "--max-time", str(timeout),
             "-w", "%{http_code} %{time_total}", url],
            timeout=timeout + 2)
        code_s, t_s = (r.stdout.strip().split() + ["0", "0"])[:2]
        return True, int(code_s or 0), int(float(t_s or 0) * 1000)
    except Exception:
        return False, 0, 0


def speed_test(timeout=12):
    """Download-тест по SPEED_MIRRORS -> (ok, mbps_str, source).

    Первое доступное зеркало; curl --max-time обрывает загрузку —
    меряется средняя скорость за окно, а не полный размер файла.
    """
    for url in SPEED_MIRRORS:
        try:
            r = process.run(
                ["curl", "-s", "-o", "/dev/null",
                 "--connect-timeout", "4", "--max-time", str(timeout),
                 "-w", "%{speed_download}", url],
                timeout=timeout + 3)
            speed = float(r.stdout.strip() or 0)
        except Exception:
            speed = 0.0
        if speed > 10_000:  # > 10 кбит/с — зеркало отдало данные
            host = url.split("/", 3)[2]
            return True, fmt_speed(speed), host
    return False, "", ""


# ------------------------------------------------------------- шаги прогона

def _step_lan():
    info = lan_info()
    details = [
        "Интерфейс: %s" % (info["iface"] or "?"),
        "Адрес: %s" % (info["cidr"] or "?"),
        "Шлюз: %s" % ("%s (dev %s)" % (info["gw"], info["gw_dev"])
                       if info["gw"] else "не задан"),
        "DNS: %s" % (", ".join(info["dns"]) or "не заданы"),
        "Источник адреса: %s" % info["dhcp"],
    ]
    if not info["ip"]:
        return ("fail", "нет глобального IPv4-адреса", details)
    if not info["gw"]:
        return ("fail", "шлюз не задан — LAN может быть, но маршрута нет",
                details)
    return ("ok", "%s → шлюз %s" % (info["ip"], info["gw"]), details)


def _step_gateway(gw):
    if not gw:
        return ("skip", "шлюз не найден — шаг пропущен", [])
    st = ping_stats(gw, count=3, timeout=2)
    details = ["ping %s ×%d: потери %.0f%%" % (gw, st["sent"], st["loss_pct"])]
    if st["avg"] is not None:
        details.append("задержка min/avg/max = %.1f/%.1f/%.1f ms"
                       % (st["min"], st["avg"], st["max"]))
    if st["loss_pct"] >= 100:
        return ("fail", "роутер не отвечает", details)
    if st["loss_pct"] > 0:
        return ("warn", "роутер отвечает, есть потери", details)
    return ("ok", "роутер отвечает, avg %.1f ms" % st["avg"], details)


def _step_dns():
    ok_n, results = 0, []
    for host in DNS_PROBES:
        ok, ms = dns_lookup(host)
        if ok:
            ok_n += 1
            results.append("%s — %d мс" % (host, ms))
        else:
            results.append("%s — не резолвится" % host)
    if ok_n == len(DNS_PROBES):
        return ("ok", "все имена резолвятся (%d/%d)"
                % (ok_n, len(DNS_PROBES)), results)
    if ok_n:
        return ("warn", "часть имён не резолвится (%d/%d)"
                % (ok_n, len(DNS_PROBES)), results)
    return ("fail", "ни одно имя не резолвится — DNS не работает", results)


def _step_internet():
    details = []
    for url, codes in HTTP_PROBES:
        ok, code, ms = http_probe(url)
        if ok and code in codes:
            return ("ok", "доступ есть (%s → %d за %d мс)"
                    % (url.split("/", 3)[2], code, ms), details)
        details.append("%s → %s" % (url, code or "нет ответа"))
    return ("fail", "ни одна HTTP-проба не прошла", details)


def _step_wan(online):
    if not online:
        return ("skip", "нет интернета — шаг пропущен", [])
    st = ping_stats(WAN_PING, count=5, timeout=2)
    details = ["ping %s ×%d: потери %.0f%%"
               % (WAN_PING, st["sent"], st["loss_pct"])]
    if st["avg"] is not None:
        details.append("задержка min/avg/max = %.1f/%.1f/%.1f ms"
                       % (st["min"], st["avg"], st["max"]))
    if st["loss_pct"] >= 100:
        return ("fail", "интернет-хост не отвечает (ICMP может быть "
                        "заблокирован провайдером — сверьтесь с HTTP-пробой)",
                details)
    if st["loss_pct"] >= 30 or (st["avg"] or 0) > 400:
        return ("warn", "связь нестабильна", details)
    if st["loss_pct"] > 0:
        return ("warn", "есть потери пакетов", details)
    return ("ok", "потерь нет, avg %.1f ms" % st["avg"], details)


def _step_speed(online):
    if not online:
        return ("skip", "нет интернета — тест пропущен", [])
    ok, speed, src = speed_test()
    if ok:
        return ("ok", "%s (зеркало %s)" % (speed, src), [])
    return ("warn", "тестовые зеркала недоступны (скорость не измерена)",
            [])


# ------------------------------------------------------------------- job

class DiagnoseJob:
    def __init__(self, want_speed=True):
        self.want_speed = want_speed
        self.steps = []
        for meta in STEPS:
            st = dict(meta)
            st.update({"status": "pending", "summary": "", "details": []})
            self.steps.append(st)
        self.current = None
        self.error = None
        self.started = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        self.finished = None
        self._thread = None

    def start_async(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _get(self, sid):
        for st in self.steps:
            if st["id"] == sid:
                return st
        return None

    def _set(self, sid, status, summary, details=None):
        st = self._get(sid)
        if st:
            st["status"] = status
            st["summary"] = summary
            st["details"] = details or []

    def _run(self):
        try:
            self.current = "lan"
            status, summary, details = _step_lan()
            self._set("lan", status, summary, details)
            info = lan_info()
            gw = info["gw"]

            self.current = "gateway"
            status, summary, details = _step_gateway(gw)
            self._set("gateway", status, summary, details)

            self.current = "dns"
            status, summary, details = _step_dns()
            self._set("dns", status, summary, details)

            self.current = "internet"
            status, summary, details = _step_internet()
            self._set("internet", status, summary, details)
            online = status == "ok"

            self.current = "wan"
            status, summary, details = _step_wan(online)
            self._set("wan", status, summary, details)

            self.current = "speed"
            if self.want_speed:
                status, summary, details = _step_speed(online)
            else:
                status, summary, details = "skip", "отключено в настройках", []
            self._set("speed", status, summary, details)
        except Exception as e:
            self.error = str(e)
        finally:
            self.current = None
            self.finished = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    def status(self):
        verdict = verdict_of(self.steps)
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "started": self.started,
            "finished": self.finished,
            "current": self.current,
            "error": self.error,
            "steps": self.steps,
            "verdict": verdict,
        }


def verdict_of(steps):
    """Итог по шагам: {'status': ok|warn|fail|skip|pending, 'text': str}."""
    failed = [s["title"] for s in steps if s["status"] == "fail"]
    warned = [s["title"] for s in steps if s["status"] == "warn"]
    if failed:
        return {"status": "fail",
                "text": "Проблемы: " + "; ".join(failed)}
    if warned:
        return {"status": "warn",
                "text": "Есть замечания: " + "; ".join(warned)}
    if not steps:
        return {"status": "pending",
                "text": "Проверка ещё не запускалась"}
    done = [s for s in steps if s["status"] in ("ok", "skip")]
    if len(done) == len(steps):
        return {"status": "ok", "text": "Всё в порядке"}
    return {"status": "pending", "text": "Проверка выполняется…"}


_lock = threading.Lock()
_job = None


def status():
    with _lock:
        if _job is None:
            return {"running": False, "steps": [
                dict(meta, status="pending", summary="", details=[])
                for meta in STEPS],
                "verdict": verdict_of([]),
                "started": None, "finished": None, "current": None,
                "error": None}
        return _job.status()


def start(want_speed=True):
    global _job
    with _lock:
        if _job is not None and _job.status()["running"]:
            raise RuntimeError("диагностика уже выполняется")
        _job = DiagnoseJob(want_speed=want_speed)
        _job.start_async()
        return _job

# -*- coding: utf-8 -*-
"""Сканер открытых портов — ядро модуля portscan.

Диапазон IP задаёт пользователь (CIDR / список через запятую /
a.b.c.d-e.f.g.h / a.b.c.d-N), порты: all | top | 1-1024 | 22,80,443,
8000-8100. Результат — текстовый файл на диске:

    results_dir() = settings["portscan"]["results_dir"]
                    либо /srv/lan-discovery/portscan (HDD смонтирован)
                    либо <prefix>/portscan

Один скан за раз (singleton), отмена — engine.stop(), прогресс — status().
Файлы результатов пишутся ТОЛЬКО на смонтированный HDD/SSD (без eMMC/SD):
storage_info() -> {"ok": bool, "base": path, "reason": str}.
"""
import ipaddress
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from core import config

MAX_HOSTS = 4096
WORKERS = 100
BIG_JOB = 500_000  # hosts*ports выше — старт только с confirm

TOP_PORTS = (
    21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 993, 995,
    1723, 3306, 3389, 5432, 5900, 6379, 8000, 8080, 8081, 8443, 8888,
    9090, 9091, 9200, 11211, 19999,
)

_FILE_RE = re.compile(r"^portscan-[\d\-]{15}\.txt$")


# ---------------------------------------------------------------- парсинг

def parse_targets(spec):
    """CIDR | ip | ip-ip | ip-N | список через запятую -> list[str] IP."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("укажите диапазон IP")
    seen = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        for ip in _expand_part(part):
            seen.add(ip)
    hosts = sorted(seen, key=lambda x: int(ipaddress.IPv4Address(x)))
    if not hosts:
        raise ValueError("диапазон пуст")
    if len(hosts) > MAX_HOSTS:
        raise ValueError("слишком много хостов: %d (лимит %d)"
                         % (len(hosts), MAX_HOSTS))
    return hosts


def _expand_part(part):
    if "/" in part:
        net = ipaddress.ip_network(part, strict=False)
        if net.version != 4:
            raise ValueError("поддерживаются только IPv4: %s" % part)
        addrs = list(net.hosts())
        if not addrs:  # /32 и /31
            addrs = list(net)
        return [str(a) for a in addrs]
    if "-" in part:
        left, right = part.split("-", 1)
        left, right = left.strip(), right.strip()
        ipaddress.IPv4Address(left)  # валидация
        if right.isdigit():
            prefix = left.rsplit(".", 1)[0]
            end = int(ipaddress.IPv4Address("%s.%s" % (prefix, right)))
        else:
            ipaddress.IPv4Address(right)
            end = int(ipaddress.IPv4Address(right))
        start = int(ipaddress.IPv4Address(left))
        if end < start:
            start, end = end, start
        if end - start + 1 > MAX_HOSTS:
            raise ValueError("диапазон шире лимита %d хостов" % MAX_HOSTS)
        return [str(ipaddress.IPv4Address(i)) for i in range(start, end + 1)]
    ipaddress.IPv4Address(part)  # валидация одиночного IP
    return [part]


def parse_ports(spec):
    """all | top | 1-1024 | 22,80,443 | 8000-8100 -> sorted list[int]."""
    spec = (spec or "top").strip().lower()
    if spec in ("all", "-", "1-65535"):
        return list(range(1, 65536))
    if spec in ("top", "common"):
        return sorted(TOP_PORTS)
    ports = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            if not (1 <= lo <= hi <= 65535):
                raise ValueError("неверный диапазон портов: %s" % part)
            ports.update(range(lo, hi + 1))
        else:
            p = int(part)
            if not 1 <= p <= 65535:
                raise ValueError("неверный порт: %s" % part)
            ports.add(p)
    if not ports:
        raise ValueError("укажите порты: all / top / 1-1024 / 22,80,443")
    return sorted(ports)


# ---------------------------------------------------------------- файлы

def _base_dir():
    """Каталог файлов результатов: settings portscan.results_dir
    либо /srv/lan-discovery/portscan (HDD), иначе <prefix>/portscan."""
    custom = (config.get("portscan", "results_dir", "") or "").strip()
    if custom:
        return custom
    if os.path.isdir("/srv/lan-discovery"):
        return "/srv/lan-discovery/portscan"
    return os.path.join(config.PREFIX, "portscan")


def _real_disks():
    """set mountpoint-ов настоящих HDD/SSD (/dev/sd*, nvme*, hd*, vdx*).

    Исключает eMMC/SD (mmcblk*), loop/zram/cdrom и виртуальные fs —
    запись результата скана разрешена только на физический диск.
    """
    out = set()
    try:
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev, mnt = parts[0], parts[1]
                if not dev.startswith("/dev/"):
                    continue
                base = dev.rsplit("/", 1)[-1]
                if base.startswith(("mmcblk", "loop", "zram", "sr", "fd",
                                    "dm-", "md")):
                    continue
                if base.startswith(("sd", "nvme", "hd", "vd", "xvd")):
                    out.add(os.path.realpath(mnt))
    except Exception:
        pass
    return out


def storage_info(path=None):
    """Проверка: путь лежит на смонтированном HDD/SSD.

    {"ok": bool, "base": path, "reason": str} — reason при ok=False.
    """
    base = os.path.realpath(path or _base_dir())
    disks = _real_disks()
    if not disks:
        return {"ok": False, "base": base,
                "reason": "HDD/SSD не подключён — запись результата "
                          "сканирования доступна только на физический диск"}
    for mnt in sorted(disks, key=len, reverse=True):
        if base == mnt or base.startswith(mnt.rstrip("/") + "/"):
            return {"ok": True, "base": base, "reason": ""}
    return {"ok": False, "base": base,
            "reason": "каталог %s не на HDD/SSD — укажите путь на диск "
                      "(portscan.results_dir в settings.json)" % base}


def results_dir():
    """Каталог результатов (создаётся) либо None, если HDD/SSD нет."""
    info = storage_info()
    if not info["ok"]:
        return None
    try:
        os.makedirs(info["base"], exist_ok=True)
    except Exception:
        return None
    return info["base"]


def _safe_name(name):
    name = os.path.basename(name or "")
    if not _FILE_RE.match(name):
        return None
    d = results_dir()
    if not d:
        return None
    path = os.path.join(d, name)
    return path if os.path.isfile(path) else None


def list_files():
    d = results_dir()
    if not d:
        return []
    out = []
    try:
        for name in sorted(os.listdir(d), reverse=True):
            if not _FILE_RE.match(name):
                continue
            st = os.stat(os.path.join(d, name))
            out.append({
                "name": name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime)
                .strftime("%d.%m.%Y %H:%M:%S"),
            })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- сканер

def _probe(ip, port, timeout):
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.time()
        rc = s.connect_ex((ip, port))
        ms = int((time.time() - t0) * 1000)
        return rc == 0, ms
    except Exception:
        return False, 0
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _service(port):
    try:
        return socket.getservbyport(port, "tcp")
    except Exception:
        return ""


class ScanJob:
    def __init__(self, hosts, ports, timeout, range_spec, ports_spec):
        self.hosts = hosts
        self.ports = ports
        self.timeout = timeout
        self.range_spec = range_spec
        self.ports_spec = ports_spec
        self.total = len(hosts) * len(ports)
        self.done = 0
        self.open = 0
        self.hosts_done = 0
        self.current = ""
        self.error = None
        self.cancelled = False
        self.started = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        self.finished = None
        self.file = None
        self._cancel = threading.Event()
        self._thread = None

    def stop(self):
        self._cancel.set()

    def start_async(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        d = results_dir()
        if not d:
            self.error = "HDD/SSD не найден — запись результата отменена"
            self.finished = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            return
        self.file = "portscan-%s.txt" % datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(d, self.file)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# LAN Discovery — скан открытых портов\n")
                f.write("# started: %s\n" % self.started)
                f.write("# range: %s (%d hosts)\n"
                        % (self.range_spec, len(self.hosts)))
                f.write("# ports: %s (%d ports), timeout %.2fs, workers %d\n"
                        % (self.ports_spec, len(self.ports),
                           self.timeout, WORKERS))
                f.write("# ---\n")
                f.flush()
                with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                    for ip in self.hosts:
                        if self._cancel.is_set():
                            break
                        self.current = ip
                        futs = {ex.submit(_probe, ip, p, self.timeout): p
                                for p in self.ports}
                        for fut in as_completed(futs):
                            port = futs[fut]
                            self.done += 1
                            try:
                                opened, ms = fut.result()
                            except Exception:
                                opened, ms = False, 0
                            if opened:
                                self.open += 1
                                svc = _service(port)
                                f.write("%s:%d/tcp open%s (%d ms)\n"
                                        % (ip, port,
                                           " " + svc if svc else "", ms))
                                f.flush()
                        self.hosts_done += 1
                if self._cancel.is_set():
                    self.cancelled = True
                f.write("# ---\n")
                f.write("# finished: %s\n"
                        % datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
                f.write("# hosts scanned: %d/%d, open: %d, done: %d/%d\n"
                        % (self.hosts_done, len(self.hosts),
                           self.open, self.done, self.total))
                if self.cancelled:
                    f.write("# cancelled by user\n")
        except Exception as e:
            self.error = str(e)
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("# ERROR: %s\n" % e)
            except Exception:
                pass
        finally:
            self.finished = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            self.current = ""

    def status(self):
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "range": self.range_spec,
            "ports": self.ports_spec,
            "hosts": len(self.hosts),
            "ports_count": len(self.ports),
            "total": self.total,
            "done": self.done,
            "open": self.open,
            "hosts_done": self.hosts_done,
            "current": self.current,
            "file": self.file,
            "started": self.started,
            "finished": self.finished,
            "cancelled": self.cancelled,
            "error": self.error,
        }


_lock = threading.Lock()
_job = None


def status():
    with _lock:
        st = _job.status() if _job else {"running": False}
    st["storage"] = storage_info()
    return st


def start(hosts, ports, timeout, range_spec, ports_spec):
    global _job
    info = storage_info()
    if not info["ok"]:
        raise RuntimeError(info["reason"])
    with _lock:
        if _job is not None and _job.status()["running"]:
            raise RuntimeError("сканирование уже выполняется")
        _job = ScanJob(hosts, ports, timeout, range_spec, ports_spec)
        _job.start_async()
        return _job


def stop():
    with _lock:
        if _job is not None:
            _job.stop()

# -*- coding: utf-8 -*-
"""Capabilities layer (STEP 7 + PHASE 2.0-1 «Capabilities 2.0»).

Read-only, без зависимостей от app (как core/hardware.py).

Формат capability-записи:
    {"state": "present" | "absent" | "unknown",
     "reliability": "measured"   -- значение прочитано из источника
                  | "detected"    -- наличие/отсутствие определено проверкой
                  | "unverified", -- источник недоступен/не подтверждён
      "value": <дополнительно, только если есть проверенное значение>}

Ничего не угадывается (правило №0 спеки 2.0):
- корень источника (/sys, /proc, /dev) недоступен → unknown/unverified;
- поиск выполнен и ничего не дал → absent/detected (отсутствие — только
  после неудачного поиска);
- содержимое источника прочитано → value, reliability=measured.

Таксономия 2.0 (PHASE 2.0-1), группы collect():
    board    — модель/архитектура платы (1.1, без изменений)
    storage  — emmc/sd/hdd (1.1) + local/removable/smart (2.0)
    thermal  — термозона/температура (1.1)
    network  — ethernet / wifi / wifi_ap / multiple_interfaces
    hardware — usb / gpio / uart / rs485 / i2c / spi / onewire
    radio    — sdr / subghz
    camera   — usb / ip
    media    — audio / video
    service  — systemd / docker
    tools    — внешние бинарие (1.1 + hostapd/docker в 2.0)
    checked_at — метка времени проверки (1.1)

Совместимость: старые ключи 1.1 (board/storage.emmc/sd/hdd/thermal/tools)
сохранены байт-в-байт; новые группы — аддитивны (API/UI/манифесты модулей
compute_status на новые ключи не завязаны; manifest `hardware.storage`
может требовать и "local"/"removable" — lookup по подсловарю сохранён).
"""
import glob as _globmod
import json
import os
import platform as _platform
import re
import shutil
import socket
import time

from core.hardware import (
    emmc_device,
    hdd_device,
    sd_device,
    thermal_temp,
    thermal_zone_path,
)

# Единый источник проб внешних бинарей.
# app.py импортирует этот кортеж как CAPABILITY_PROBES (probe для /api/health).
TOOL_PROBES = (
    "nmap", "ping", "tracepath", "host", "iw",
    "bluetoothctl", "smartctl", "ffmpeg", "mpv", "lsblk",
    "hostapd", "docker",
)

VALID_STATES = ("present", "absent", "unknown")
VALID_RELIABILITY = ("measured", "detected", "unverified")

# Известные USB ID для radio.sdr (только подтверждённые чипы, без догадок).
_SDR_USB = {
    ("0bda", "2832"): "rtl-sdr",
    ("0bda", "2838"): "rtl-sdr",
    ("1d50", "6089"): "hackrf",
    ("1d50", "60a1"): "airspy",
}
# Flipper Zero CDC (sub-ГГц-программатор).
_FLIPPER_USB = ("0483", "5740")
_HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
_ALSA_CARDS = "/proc/asound/cards"
_CAMERAS_CONFIG = "/etc/lan-discovery/cameras.json"
_DOCKER_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")

_TTL = 30
_TOOLS_TTL = 300
_cache = {"data": None, "ts": 0}
_tools_cache = {"data": None, "ts": 0}


def _cap(state, reliability, value=None):
    d = {"state": state, "reliability": reliability}
    if value is not None:
        d["value"] = value
    return d


def _glob(pattern):
    """glob с проверкой источника: None — корень паттерна недоступен.

    Если ни один компонент пути не существует (не-Linux, sysfs не смонтирован)
    → None (источник недоступен, а не «ничего не найдено»). Если корень
    существует, но совпадений нет → [] (неудачный поиск → absent/detected).
    """
    try:
        root = pattern.split("*")[0].rstrip("/\\")
        while root and not os.path.exists(root):
            parent = os.path.dirname(root)
            if parent == root:
                return None
            root = parent
        return sorted(_globmod.glob(pattern))
    except Exception:
        return None


def probe_tools():
    """Наличие внешних бинарей в PATH (кэш 300 с)."""
    now = time.time()
    if _tools_cache["data"] is not None and now - _tools_cache["ts"] < _TOOLS_TTL:
        return _tools_cache["data"]
    tools = {}
    for name in TOOL_PROBES:
        try:
            found = shutil.which(name) is not None
        except Exception:
            found = None
        if found is None:
            tools[name] = _cap("unknown", "unverified")
        else:
            tools[name] = _cap("present" if found else "absent", "detected")
    _tools_cache["data"] = tools
    _tools_cache["ts"] = now
    return tools


def _storage(fn):
    """Хранилище из core.hardware: имя устройства или отсутствие."""
    try:
        dev = fn()
    except Exception:
        return _cap("unknown", "unverified")
    if dev:
        return _cap("present", "detected", value=dev)
    return _cap("absent", "detected")


def _board():
    """Модель платы: /proc/device-tree/model = measured; иначе hostname — unverified."""
    base = {"arch": _platform.machine(), "system": _platform.system()}
    try:
        with open("/proc/device-tree/model", "rb") as f:
            m = f.read().replace(b"\x00", b"").decode("utf-8", "replace").strip()
        if m:
            return dict(base, model=m, reliability="measured")
    except Exception:
        pass
    try:
        return dict(base, model=socket.gethostname(), reliability="unverified")
    except Exception:
        return dict(base, model=None, reliability="unverified")


def _thermal():
    """Термозона + температура: measured если значение прочитано."""
    try:
        zone = thermal_zone_path()
    except Exception:
        return _cap("unknown", "unverified")
    if not zone:
        return _cap("absent", "detected")
    try:
        temp = thermal_temp()
    except Exception:
        temp = None
    if temp is None:
        return _cap("unknown", "unverified", value=zone)
    return _cap("present", "measured", value=temp)


# --- storage 2.0 ----------------------------------------------------------

def _storage_local(storage):
    """storage.local: есть ли локальное (встроенное) хранилище."""
    states = [storage[k]["state"] for k in ("emmc", "sd", "hdd")]
    if "present" in states:
        return _cap("present", "detected")
    if "unknown" in states:
        return _cap("unknown", "unverified")
    return _cap("absent", "detected")


def _storage_removable():
    """Съёмные носители: /sys/block/*/removable == 1 (значение из sysfs)."""
    paths = _glob("/sys/block/*/removable")
    if paths is None:
        return _cap("unknown", "unverified")
    if not paths:
        return _cap("absent", "detected")
    devs, read_ok = [], False
    for p in paths:
        try:
            with open(p) as f:
                read_ok = True
                if f.read().strip() == "1":
                    devs.append(p.split("/")[3])
        except Exception:
            continue
    if not read_ok:
        return _cap("unknown", "unverified")
    if devs:
        return _cap("present", "measured", value=sorted(set(devs)))
    return _cap("absent", "detected")


def _storage_smart():
    """SMART: smartctl в PATH (проверка поиска, не угадывание)."""
    try:
        found = shutil.which("smartctl") is not None
    except Exception:
        return _cap("unknown", "unverified")
    if found:
        return _cap("present", "detected", value="smartctl")
    return _cap("absent", "detected")


# --- network --------------------------------------------------------------

def _net_ifaces():
    """(wired, wireless) — физические интерфейсы из sysfs.

    None — /sys/class/net недоступен. Виртуальные (lo, veth, docker0,
    bridge): нет device/ и нет wireless/ → пропускаются.
    """
    if not os.path.isdir("/sys/class/net"):
        return None
    try:
        names = os.listdir("/sys/class/net")
    except Exception:
        return None
    wired, wireless = [], []
    for n in sorted(names):
        if n == "lo":
            continue
        base = "/sys/class/net/" + n
        is_wifi = os.path.isdir(base + "/wireless")
        if not is_wifi and not os.path.exists(base + "/device"):
            continue
        (wireless if is_wifi else wired).append(n)
    return wired, wireless


def _wifi_ap(has_wifi):
    """Wi-Fi AP: только при наличии Wi-Fi-интерфейса и ПО (hostapd/конфиг)."""
    if not has_wifi:
        return _cap("absent", "detected")
    try:
        if shutil.which("hostapd"):
            return _cap("present", "detected", value="hostapd")
        if os.path.isfile(_HOSTAPD_CONF):
            return _cap("present", "detected", value=_HOSTAPD_CONF)
    except Exception:
        return _cap("unknown", "unverified")
    return _cap("absent", "detected")


def _network():
    try:
        ifaces = _net_ifaces()
    except Exception:
        ifaces = None
    if ifaces is None:
        return {
            k: _cap("unknown", "unverified")
            for k in ("ethernet", "wifi", "wifi_ap", "multiple_interfaces")
        }
    wired, wireless = ifaces

    def named(names):
        if names:
            return _cap("present", "detected", value=names)
        return _cap("absent", "detected")

    count = len(wired) + len(wireless)
    return {
        "ethernet": named(wired),
        "wifi": named(wireless),
        "wifi_ap": _wifi_ap(bool(wireless)),
        "multiple_interfaces": _cap(
            "present" if count >= 2 else "absent", "measured", value=count),
    }


# --- hardware -------------------------------------------------------------

def _scan(patterns):
    """Сканирование glob-паттернов: имена → present/detected(value).

    Пусто → absent/detected; любой паттерн-источник недоступен →
    unknown/unverified.
    """
    names = []
    for pat in patterns:
        got = _glob(pat)
        if got is None:
            return _cap("unknown", "unverified")
        for path in got:
            base = os.path.basename(path.rstrip("/"))
            if base and base not in names:
                names.append(base)
    if names:
        return _cap("present", "detected", value=sorted(names))
    return _cap("absent", "detected")


def _rs485():
    """RS-485: /sys/class/tty/<tty>/rs485 (драйвер экспортует флаг)."""
    paths = _glob("/sys/class/tty/*/rs485")
    if paths is None:
        return _cap("unknown", "unverified")
    ttys = sorted({p.split("/")[4] for p in paths if len(p.split("/")) > 4})
    if ttys:
        return _cap("present", "detected", value=ttys)
    return _cap("absent", "detected")


def _hardware():
    return {
        "usb": _scan(["/sys/bus/usb/devices/usb*"]),
        "gpio": _scan(["/dev/gpiochip*", "/sys/class/gpio/gpiochip*"]),
        "uart": _scan(["/dev/ttyS*", "/dev/ttyAMA*", "/dev/ttyUSB*",
                       "/dev/ttyACM*", "/dev/ttymxc*"]),
        "rs485": _rs485(),
        "i2c": _scan(["/sys/class/i2c-dev/*", "/dev/i2c-*"]),
        "spi": _scan(["/sys/class/spi_master/*", "/dev/spidev*"]),
        "onewire": _scan(["/sys/bus/w1/devices/*"]),
    }


# --- radio ----------------------------------------------------------------

def _usb_pairs():
    """Список (idVendor, idProduct) из sysfs; None — источник недоступен."""
    if not os.path.isdir("/sys/bus/usb/devices"):
        return None
    try:
        names = os.listdir("/sys/bus/usb/devices")
    except Exception:
        return None
    pairs = []
    for d in sorted(names):
        base = "/sys/bus/usb/devices/" + d + "/"
        try:
            with open(base + "idVendor") as f:
                vid = f.read().strip().lower()
            with open(base + "idProduct") as f:
                pid = f.read().strip().lower()
        except Exception:
            continue
        if vid and pid:
            pairs.append((vid, pid))
    return pairs


def _sdr():
    """SDR: известные USB ID (rtl-sdr/hackrf/airspy) — только подтверждённые."""
    try:
        pairs = _usb_pairs()
    except Exception:
        return _cap("unknown", "unverified")
    if pairs is None:
        return _cap("unknown", "unverified")
    found = sorted({
        name for vid, pid in pairs
        for (mv, mp), name in _SDR_USB.items()
        if (vid, pid) == (mv, mp)
    })
    if found:
        return _cap("present", "detected", value=found)
    return _cap("absent", "detected")


def _subghz():
    """Sub-GHz: Flipper CDC → present; есть SPI → отсутствие не доказать."""
    try:
        pairs = _usb_pairs()
        spi = _scan(["/sys/class/spi_master/*", "/dev/spidev*"])
    except Exception:
        return _cap("unknown", "unverified")
    if pairs is None:
        return _cap("unknown", "unverified")
    if _FLIPPER_USB in pairs:
        return _cap("present", "detected", value="flipper zero")
    if spi["state"] == "present":
        # CC1101 может висеть на SPI — отсутствие подтвердить нельзя
        return _cap("unknown", "unverified")
    if spi["state"] == "unknown":
        return _cap("unknown", "unverified")
    return _cap("absent", "detected")


def _radio():
    return {"sdr": _sdr(), "subghz": _subghz()}


# --- camera ---------------------------------------------------------------

def _camera_usb():
    """USB-камеры: интерфейсный класс UVC (0e) в sysfs, иначе узлы V4L."""
    paths = _glob("/sys/bus/usb/devices/*/*/bInterfaceClass")
    if paths is None:
        return _cap("unknown", "unverified")
    devs = []
    for p in paths:
        try:
            with open(p) as f:
                if f.read().strip() != "0e":
                    continue
        except Exception:
            continue
        dev = os.path.basename(os.path.dirname(p)).split(":")[0]
        if dev and dev not in devs:
            devs.append(dev)
    if devs:
        return _cap("present", "detected", value=sorted(devs))
    v4l = _glob("/sys/class/video4linux/*")
    if v4l is None:
        return _cap("unknown", "unverified")
    names = sorted({os.path.basename(v.rstrip("/")) for v in v4l if v})
    if names:
        return _cap("present", "detected", value=names)
    return _cap("absent", "detected")


def _camera_ip():
    """IP-камеры: заведены в конфиге панели (сеть не сканируем)."""
    try:
        if not os.path.isfile(_CAMERAS_CONFIG):
            # не сканируем сеть — отсутствие подтвердить не можем
            return _cap("unknown", "unverified")
        with open(_CAMERAS_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return _cap("unknown", "unverified")
    if isinstance(cfg, dict):
        items = cfg.get("cameras") or cfg.get("items") or []
    elif isinstance(cfg, list):
        items = cfg
    else:
        items = []
    if items:
        return _cap("present", "measured", value=len(items))
    return _cap("absent", "detected")


def _camera():
    return {"usb": _camera_usb(), "ip": _camera_ip()}


# --- media ----------------------------------------------------------------

def _audio():
    """Аудио (ALSA): /proc/asound/cards — имена карт (measured)."""
    if not os.path.isdir("/proc"):
        return _cap("unknown", "unverified")
    try:
        with open(_ALSA_CARDS) as f:
            text = f.read()
    except FileNotFoundError:
        # ALSA не поддерживается ядром — подтверждённое отсутствие
        return _cap("absent", "detected")
    except Exception:
        return _cap("unknown", "unverified")
    cards = [m.strip() for m in
             re.findall(r"^\s*\d+\s+\[([^\]]+)\]", text, re.M)]
    if cards:
        return _cap("present", "measured", value=cards)
    return _cap("absent", "detected")


def _media():
    return {
        "audio": _audio(),
        "video": _scan(["/dev/dri/*"]),
    }


# --- service --------------------------------------------------------------

def _systemd():
    """systemd: PID1-дерево /run/systemd/system (только Linux)."""
    try:
        if not os.path.isdir("/proc"):
            return _cap("unknown", "unverified")
        present = os.path.isdir("/run/systemd/system")
    except Exception:
        return _cap("unknown", "unverified")
    return _cap("present" if present else "absent", "detected")


def _docker():
    """Docker: сокет → present; только CLI → unknown (демон не подтверждён)."""
    found_sock, sock_unknown = [], False
    for p in _DOCKER_SOCKETS:
        g = _glob(p)
        if g is None:
            sock_unknown = True
        else:
            found_sock.extend(g)
    if found_sock:
        return _cap("present", "detected", value="docker.sock")
    try:
        cli = shutil.which("docker") is not None
    except Exception:
        cli = None
    if cli is None:
        return _cap("unknown", "unverified")
    if cli:
        return _cap("unknown", "unverified", value="docker CLI без сокета")
    if sock_unknown:
        return _cap("unknown", "unverified")
    return _cap("absent", "detected")


def _service():
    return {"systemd": _systemd(), "docker": _docker()}


# --- aggregate ------------------------------------------------------------

def collect():
    """Агрегат всех capabilities (кэш 30 с)."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    storage = {
        "emmc": _storage(emmc_device),
        "sd": _storage(sd_device),
        "hdd": _storage(hdd_device),
    }
    storage["local"] = _storage_local(storage)
    storage["removable"] = _storage_removable()
    storage["smart"] = _storage_smart()
    data = {
        "board": _board(),
        "storage": storage,
        "thermal": _thermal(),
        "network": _network(),
        "hardware": _hardware(),
        "radio": _radio(),
        "camera": _camera(),
        "media": _media(),
        "service": _service(),
        "tools": probe_tools(),
        "checked_at": time.strftime("%d.%m.%Y %H:%M:%S"),
    }
    _cache["data"] = data
    _cache["ts"] = now
    return data

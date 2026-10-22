# -*- coding: utf-8 -*-
"""Hardware-инструменты (Flipper-направление) — ядро модуля flipper.

Разведка инструментов и железа (rtl-sdr, USB-serial, программаторы,
NFC/IR), три рабочих инструмента поверх детекта:

  pager  — live-лента сообщений rtl_433 (433/868/915 МГц, SDR-донгл);
  serial — список /dev/tty* + монитор (pyserial), консольные порты
           (из /proc/consoles) помечаются и не открываются на запись;
  prog   — программаторы avrdude (AVR), esptool (ESP8226/ESP32),
           stm32flash (STM32): детект чипа, flash из загруженного файла.

Всё ставится по кнопке из UI (apt/pip, admin): без железа страница
показывает «не подключено» с подсказкой, а не ошибку. Команды
программаторов собираются только из whitelist-полей (никаких
произвольных строк) — паранойя безопасности панели.
"""
import glob
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

from core import config
from core import process

PREFIX = config.PREFIX
VENV_PY = os.path.join(PREFIX, "venv", "bin", "python")
UPLOAD_DIR = "/tmp/flipper"
MAX_LOG = 200          # строк в ring-buffer логов
MAX_UPLOAD = 32 * 1024 * 1024

# ---------------------------------------------------------------- детект

APT_TOOLS = {
    "rtl_sdr": ("rtl-sdr", "rtl_sdr"),
    "rtl_433": ("rtl-433", "rtl_433"),
    "avrdude": ("avrdude", "avrdude"),
    "esptool": ("esptool", "esptool.py"),
    "stm32flash": ("stm32flash", "stm32flash"),
    "libnfc": ("libnfc-utils", "nfc-list"),
}
PIP_TOOLS = {
    "pyserial": ("pyserial", "serial"),
}

# Консольные порты (последовательные консоли пр.) — не открывать в мониторе.
_CONSOLE_RE = re.compile(r"(\S*console\S*|\bttyS\d+\b)")

RTL_USB_IDS = ("0bda:2832", "0bda:2838", "0bda:2834", "0bda:2836",
               "0bda:2839", "0bda:283d")


def _which(name):
    return shutil.which(name)


def _apt_installed(pkg):
    try:
        r = process.run(["dpkg", "-s", pkg], timeout=8)
        return r.returncode == 0 and "Status: install ok installed" in (r.stdout or "")
    except Exception:
        return False


def _pip_installed(mod):
    return importlib.util.find_spec(mod) is not None


def _venv_pip_installed(mod):
    """Есть ли python-пакет в venv панели (не в системном python)."""
    if not os.path.isfile(VENV_PY):
        return _pip_installed(mod)
    try:
        r = process.run([VENV_PY, "-c", "import %s" % mod], timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def _console_ports():
    """Активные консольные порты ядра из /proc/consoles."""
    out = set()
    try:
        with open("/proc/consoles", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                name = line.split()[0] if line.split() else ""
                if name:
                    out.add(name)
    except Exception:
        pass
    return out


def list_serial():
    """[{name, path, console, busy}] — встроенные и USB/ACM UART."""
    consoles = _console_ports()
    ports = []
    patterns = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS[0-9]*")
    seen = set()
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            name = os.path.basename(path)
            if name in seen:
                continue
            seen.add(name)
            ports.append({
                "name": name,
                "path": path,
                "console": name in consoles or ("ttyS" + name[-1]) in consoles,
                "busy": _port_busy(path),
            })
    return ports


def _port_busy(path):
    try:
        r = process.run(["fuser", path], timeout=3)
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


def _rtl_present():
    try:
        out = process.out(["lsusb"], timeout=5).lower()
        return any(usb_id in out for usb_id in RTL_USB_IDS)
    except Exception:
        return False


def _nfc_present():
    try:
        out = process.out(["lsusb"], timeout=5).lower()
        return ("pn533" in out) or ("1050:00" in out)  # Yubikey-ish nfc + pn533
    except Exception:
        return False


def _ir_present():
    """IR-приёмник: lirc-контроллер или gpio-ir RX в device-tree."""
    if os.path.isdir("/sys/class/lirc"):
        for entry in os.listdir("/sys/class/lirc"):
            if entry:
                return True
    for pat in ("/proc/device-tree/*lirc*", "/sys/firmware/devicetree/base/*lirc*"):
        if glob.glob(pat):
            return True
    return False


def detect():
    """Полная разведка: инструменты + железо. Для UI — карточка статуса."""
    tools = {}
    for key, (pkg, binary) in APT_TOOLS.items():
        present = _which(binary) is not None
        tools[key] = {"present": present, "pkg": pkg,
                      "installed": _apt_installed(pkg) if not present else True}
    for key, (pkg, mod) in PIP_TOOLS.items():
        present = _venv_pip_installed(mod)
        tools[key] = {"present": present, "pkg": pkg, "installed": present}
    return {
        "tools": tools,
        "devices": {
            "rtl": _rtl_present(),
            "nfc": _nfc_present(),
            "ir": _ir_present(),
        },
        "serial": list_serial(),
        "consoles": sorted(_console_ports()),
    }


# ------------------------------------------------------------ установка

APT_ALLOWED = {"rtl-sdr", "rtl-433", "avrdude", "esptool", "stm32flash",
               "libnfc-utils", "libnfc-bin", "picocom"}
PIP_ALLOWED = {"pyserial", "esptool"}

_install_lock = threading.Lock()
_install_state = {"running": False, "pkg": "", "log": [], "done": False}


def install_log_tail(n=40):
    return _install_state["log"][-n:]


def install_status():
    return {k: _install_state[k] for k in ("running", "pkg", "done")}


def install(pkg):
    """apt-get/pip install (только whitelist). Асинхронно, лог — в UI."""
    pkg = (pkg or "").strip()
    if pkg not in APT_ALLOWED and pkg not in PIP_ALLOWED:
        raise RuntimeError("пакет не в whitelist: %s" % pkg)
    with _install_lock:
        if _install_state["running"]:
            raise RuntimeError("установка уже выполняется")
        _install_state.update(running=True, pkg=pkg, log=[], done=False)
        threading.Thread(target=_install_worker, args=(pkg,),
                         daemon=True).start()


def _install_worker(pkg):
    log = _install_state["log"]
    try:
        if pkg in APT_ALLOWED:
            cmds = [["apt-get", "update", "-qq"],
                    ["apt-get", "install", "-y", pkg]]
        elif pkg in PIP_ALLOWED:
            py = VENV_PY if os.path.isfile(VENV_PY) else sys.executable
            cmds = [[py, "-m", "pip", "install", "--quiet", pkg]]
        else:
            raise RuntimeError("пакет не в whitelist: %s" % pkg)
        for cmd in cmds:
            log.append("$ " + " ".join(shlex.quote(c) for c in cmd))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    errors="replace")
            for line in proc.stdout:
                log.append(line.rstrip())
                if len(log) > MAX_LOG:
                    del log[: len(log) - MAX_LOG]
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("команда завершилась с кодом %d: %s"
                                   % (proc.returncode, cmd[0]))
        log.append("OK: %s установлен" % pkg)
    except Exception as e:
        log.append("ERROR: %s" % e)
    finally:
        with _install_lock:
            _install_state["running"] = False
            _install_state["done"] = True


# ------------------------------------------------------------ SDR-пейджер

_R433 = {"proc": None, "msgs": deque(maxlen=MAX_LOG), "lock": threading.Lock(),
         "started": None, "error": None}

_MSG_KEEP = ("time", "model", "id", "battery", "temperature_C", "temperature_F",
             "humidity", "wind_avg_km_h", "wind_max_km_h", "rain_mm",
             "distance_m", "mic", "code", "raw_message", "brand", "cmd",
             "state", "channel", "unit", "trip", "total", "frequency",
             "rssi", "snr", "noise")


def pager_status():
    with _R433["lock"]:
        running = _R433["proc"] is not None and _R433["proc"].poll() is None
        return {"running": running, "started": _R433["started"],
                "error": _R433["error"], "count": len(_R433["msgs"])}


def pager_messages(after=0):
    """Сообщения с порядковым номером > after (порядок — вставка)."""
    with _R433["lock"]:
        return [m for m in _R433["msgs"] if m.get("_n", 0) > after]


def pager_start():
    if not _which("rtl_433"):
        raise RuntimeError("rtl_433 не установлен — поставьте rtl-433 "
                           "(кнопка в разделе «Инструменты»)")
    with _R433["lock"]:
        p = _R433["proc"]
        if p is not None and p.poll() is None:
            raise RuntimeError("пейджер уже запущен")
        _R433["error"] = None
        _R433["started"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        proc = subprocess.Popen(
            ["rtl_433", "-F", "json", "-M", "utc", "-M", "protocol"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace")
        _R433["proc"] = proc
        _R433["msgs"].clear()
    threading.Thread(target=_pager_reader, args=(proc,), daemon=True).start()


def _pager_reader(proc):
    n = 0
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                import json
                msg = json.loads(line)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            keep = {k: msg[k] for k in _MSG_KEEP if k in msg}
            n += 1
            keep["_n"] = n
            with _R433["lock"]:
                _R433["msgs"].append(keep)
    except Exception as e:
        _R433["error"] = str(e)
    finally:
        with _R433["lock"]:
            stopped_by_user = _R433["proc"] is None
            _R433["proc"] = None
        # rtl_433 выходит с кодом 0 и без dongle ("No supported devices
        # found") — отличаем «пользователь остановил» от «не запустился».
        if not stopped_by_user and n == 0 and not _R433["error"]:
            _R433["error"] = ("приёмник не запустился — проверьте, что "
                              "SDR-донгл подключён и не занят")


def pager_stop():
    with _R433["lock"]:
        p = _R433["proc"]
        _R433["proc"] = None
    if p is not None:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


# --------------------------------------------------------------- serial

SERIAL_BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400,
                460800, 921600)

_serial = {"port": None, "ser": None, "lines": deque(maxlen=MAX_LOG),
           "thread": None, "error": None, "opened": None, "lock": threading.Lock()}


def serial_status():
    with _serial["lock"]:
        return {"open": _serial["ser"] is not None,
                "port": _serial["port"], "error": _serial["error"],
                "opened": _serial["opened"]}


def serial_read(after=0):
    with _serial["lock"]:
        return [ln for ln in _serial["lines"] if ln.get("_n", 0) > after]


def serial_open(path, baud=115200):
    if not _venv_pip_installed("serial"):
        raise RuntimeError("pyserial не установлен — поставьте pyserial "
                           "(кнопка в разделе «Инструменты»)")
    consoles = _console_ports()
    name = os.path.basename(path)
    if name in consoles:
        raise RuntimeError("%s — консольная строка ядра, открытие запрещено"
                           % name)
    if not path.startswith("/dev/tty"):
        raise RuntimeError("не tty-порт: %s" % path)
    if not os.path.exists(path):
        raise RuntimeError("порт не существует: %s" % path)
    baud = int(baud) if int(baud) in SERIAL_BAUDS else 115200
    with _serial["lock"]:
        if _serial["ser"] is not None:
            raise RuntimeError("порт уже открыт: %s" % _serial["port"])
        import serial  # pyserial
        ser = serial.Serial(path, baudrate=baud, timeout=0.5)
        _serial["ser"] = ser
        _serial["port"] = path
        _serial["error"] = None
        _serial["opened"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        _serial["lines"].clear()
    _serial["thread"] = threading.Thread(target=_serial_reader, daemon=True)
    _serial["thread"].start()


def _serial_reader():
    ser = _serial["ser"]
    n = 0
    try:
        while True:
            with _serial["lock"]:
                s = _serial["ser"]
            if s is None:
                break
            try:
                raw = s.readline()
            except Exception as e:
                _serial["error"] = str(e)
                break
            if not raw:
                continue
            n += 1
            line = {"_n": n,
                    "t": datetime.now().strftime("%H:%M:%S"),
                    "len": len(raw)}
            try:
                line["text"] = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                line["hex"] = raw.hex(" ")
            with _serial["lock"]:
                _serial["lines"].append(line)
    finally:
        with _serial["lock"]:
            _serial["ser"] = None
            _serial["port"] = None


def serial_close():
    with _serial["lock"]:
        s = _serial["ser"]
        _serial["ser"] = None
    if s is not None:
        try:
            s.close()
        except Exception:
            pass


def serial_write(data):
    with _serial["lock"]:
        s = _serial["ser"]
    if s is None:
        raise RuntimeError("порт не открыт")
    s.write((data + "\n").encode("utf-8", errors="replace"))


# ------------------------------------------------------------ программаторы

AVR_PROGRAMMERS = ("arduino", "arduinoasppisp", "avrisp", "avrispmkii",
                   "buspirate", "bsd", "stk500", "stk500v1", "stk500v2",
                   "usbasp", "usbtiny", "ponser", "avr109")
AVR_MCUS = ("m328p", "m328", "m2560", "m168", "m8", "m48", "m88", "t85",
            "t84", "t13", "x128a3", "x256r35", "atmega32u4")
ESPTOOL_CHIPS = ("auto", "esp8266", "esp32", "esp32s2", "esp32s3",
                 "esp32c3", "esp32c6")
STM32_BAUDS = (115200, 57600, 38400, 19200, 9600)

_prog_lock = threading.Lock()
_prog = {"proc": None, "log": deque(maxlen=MAX_LOG), "cmd": "",
         "started": None, "error": None}


def prog_status():
    with _prog_lock:
        running = _prog["proc"] is not None and _prog["proc"].poll() is None
        return {"running": running, "cmd": _prog["cmd"],
                "started": _prog["started"], "error": _prog["error"]}


def prog_log(after=0):
    with _prog_lock:
        return [ln for ln in _prog["log"] if ln.get("_n", 0) > after]


def _prog_append(n, line):
    with _prog_lock:
        _prog["log"].append({"_n": n, "line": line})


def _prog_run(cmd, timeout=120):
    """Запуск команды программатора; лог построчно в prog_log()."""
    with _prog_lock:
        p = _prog["proc"]
        if p is not None and p.poll() is None:
            raise RuntimeError("программатор уже занят")
        _prog["cmd"] = " ".join(shlex.quote(c) for c in cmd)
        _prog["error"] = None
        _prog["started"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        _prog["log"].clear()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace")
        _prog["proc"] = proc
    threading.Thread(target=_prog_wait, args=(proc, timeout),
                     daemon=True).start()


def _prog_wait(proc, timeout):
    n = 0
    try:
        for line in proc.stdout:
            n += 1
            _prog_append(n, line.rstrip())
        proc.wait(timeout=timeout)
        rc = proc.returncode
        _prog_append(n + 1, "=== exit code %d ===" % rc)
        if rc != 0:
            _prog["error"] = "команда завершилась с кодом %d" % rc
    except Exception as e:
        _prog["error"] = str(e)
        _prog_append(n + 1, "ERROR: %s" % e)
    finally:
        with _prog_lock:
            _prog["proc"] = None


_HEX_RE = re.compile(r"^0x[0-9a-fA-F]{1,8}$")


def _safe_upload_name(name):
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:80] or "firmware.bin"


def save_upload(filename, stream):
    """Сохранить загруженный файл прошивки; путь — только в UPLOAD_DIR."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = _safe_upload_name(filename)
    path = os.path.join(UPLOAD_DIR, "%s.%d" % (name, int(time.time())))
    total = 0
    with open(path, "wb") as f:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD:
                f.close()
                os.unlink(path)
                raise RuntimeError("файл больше %d МБ" % (MAX_UPLOAD // 1024 // 1024))
            f.write(chunk)
    return path, total


def _valid_upload(path):
    real = os.path.realpath(path or "")
    return real.startswith(UPLOAD_DIR + os.sep) and os.path.isfile(real)


def prog_avrdude(firmware, programmer, mcu, port=None, baud=None,
                 extra_erase=True):
    if programmer not in AVR_PROGRAMMERS:
        raise RuntimeError("programmer не из списка")
    if mcu not in AVR_MCUS:
        raise RuntimeError("mcu не из списка")
    if not _valid_upload(firmware):
        raise RuntimeError("файл прошивки не найден")
    if not _which("avrdude"):
        raise RuntimeError("avrdude не установлен")
    cmd = ["avrdude", "-c", programmer, "-p", mcu]
    if port:
        name = os.path.basename(port)
        if not re.match(r"^tty(USB|ACM|S)\d+$", name):
            raise RuntimeError("port не из списка")
        cmd += ["-P", "/dev/" + name]
    if baud and int(baud) in SERIAL_BAUDS:
        cmd += ["-B", str(int(baud))]
    if extra_erase:
        cmd += ["-e"]
    cmd += ["-U", "flash:w:%s:i" % firmware]
    _prog_run(cmd, timeout=300)


def prog_esptool(firmware, port, chip="auto", baud=460800, action="write_flash",
                 address="0x0"):
    if chip not in ESPTOOL_CHIPS:
        raise RuntimeError("chip не из списка")
    if action not in ("write_flash", "read_flash", "chip_id", "erase_flash"):
        raise RuntimeError("action не из списка")
    if not _which("esptool.py") and not _which("esptool"):
        raise RuntimeError("esptool не установлен")
    tool = _which("esptool.py") or _which("esptool")
    if port:
        name = os.path.basename(port)
        if not re.match(r"^tty(USB|ACM)\d+$", name):
            raise RuntimeError("port не из списка (нужен USB/ACM)")
        port = "/dev/" + name
    if not port:
        raise RuntimeError("укажите port")
    baud = int(baud) if int(baud) in SERIAL_BAUDS else 460800
    cmd = [tool, "--port", port, "--baud", str(baud)]
    if chip != "auto":
        cmd += ["--chip", chip]
    if action == "write_flash":
        if not _valid_upload(firmware):
            raise RuntimeError("файл прошивки не найден")
        if not _HEX_RE.match(address or ""):
            raise RuntimeError("address должен быть 0x...")
        cmd += ["write_flash", address, firmware]
    elif action == "read_flash":
        if not _valid_upload(firmware):
            raise RuntimeError("файл не найден")
        cmd += ["read_flash", address or "0x0", "0x10000", firmware]
    elif action == "erase_flash":
        cmd += ["erase_flash"]
    else:
        cmd += ["chip_id"]
    _prog_run(cmd, timeout=300)


def prog_stm32flash(firmware, port, baud=115200, action="write"):
    if action not in ("write", "read", "info"):
        raise RuntimeError("action не из списка")
    if not _which("stm32flash"):
        raise RuntimeError("stm32flash не установлен")
    name = os.path.basename(port or "")
    if not re.match(r"^tty(USB|ACM)\d+$", name):
        raise RuntimeError("port не из списка (нужен USB/ACM)")
    port = "/dev/" + name
    baud = int(baud) if int(baud) in STM32_BAUDS else 115200
    cmd = ["stm32flash", "-b", str(baud), port]
    if action == "write":
        if not _valid_upload(firmware):
            raise RuntimeError("файл прошивки не найден")
        cmd += ["-w", firmware, "-v"]
    elif action == "read":
        if not _valid_upload(firmware):
            raise RuntimeError("файл не найден")
        cmd += ["-r", firmware, "0x10000"]
    _prog_run(cmd, timeout=300)

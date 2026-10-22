# -*- coding: utf-8 -*-
"""Disk Manager (расширение модуля «Диски», 2026-10-23).

Возможности: листинг дисков с охранниками, разделы (parted), форматирование
(mkfs), SMART (smartctl -j), проверка ФС (e2fsck -n), клонирование
(core.storage.clone_disk), запись загрузочных образов (FreeDOS 1.3
с ftp.icm.edu.pl + свои .img/.iso), в т.ч. на USB-дисководы (floppy).

Безопасность (fail-closed):
- деструктивные операции только на целых дисках (lsblk TYPE=disk);
- запрет на системный диск (тот, что смонтирован на "/") и на диски с
  любыми смонтированными разделами;
- запрет на zram/loop/fd/sr/*boot*;
- имя устройства — только [a-zA-Z0-9]+ (без путей).
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import zipfile

from core import process

STATE_FILE = "/tmp/lan-discovery-disktool.json"
IMAGE_DIR = "/var/cache/lan-discovery/disktool"
IMAGE_CUSTOM_DIR = os.path.join(IMAGE_DIR, "custom")

# FreeDOS 1.3 (zeropoint / официальное зеркало ICM.edu.pl, проверено 23.10.2026)
FREEDOS_BASE = ("https://ftp.icm.edu.pl/packages/freedos/distributions/"
                "1.3/official")
FREEDOS_CATALOG = [
    {
        "key": "fd13-floppy-144-boot",
        "title": "FreeDOS 1.3 — загрузочная дискета 1.44М (USB-дисковод)",
        "url": FREEDOS_BASE + "/FD13-FloppyEdition.zip",
        "member": "144m/x86BOOT.img",
        "kind": "floppy",
    },
    {
        "key": "fd13-floppy-120-boot",
        "title": "FreeDOS 1.3 — загрузочная дискета 1.2М",
        "url": FREEDOS_BASE + "/FD13-FloppyEdition.zip",
        "member": "120m/x86BOOT.img",
        "kind": "floppy",
    },
    {
        "key": "fd13-liteusb",
        "title": "FreeDOS 1.3 Lite — флешка (32М)",
        "url": FREEDOS_BASE + "/FD13-LiteUSB.zip",
        "member": "FD13LITE.img",
        "kind": "usb",
    },
]

_DEV_RE = re.compile(r"^[a-zA-Z0-9]+$")
_FS_TYPES = ("ext4", "vfat", "ntfs", "exfat", "ext3", "ext2", "btrfs", "xfs")
_LABEL_MAX = {"ext4": 16, "ext3": 16, "ext2": 16, "btrfs": 255, "xfs": 12,
              "vfat": 11, "ntfs": 32, "exfat": 255}


# --- утилиты --------------------------------------------------------------

def dev_path(name):
    """Полное /dev-имя с валидацией (только [a-zA-Z0-9]+)."""
    if not isinstance(name, str) or not _DEV_RE.match(name):
        raise ValueError("недопустимое имя устройства: %r" % (name,))
    return "/dev/" + name


def root_disk_name(mounts_text=None):
    """Имя диска, содержащего корень ("/"): /dev/mmcblk2p2 → mmcblk2."""
    if mounts_text is None:
        try:
            with open("/proc/mounts", "r", encoding="utf-8") as f:
                mounts_text = f.read()
        except Exception:
            return None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "/":
            src = parts[0]
            name = src.split("/")[-1]
            return re.sub(r"p?\d+$", "", name)
    return None


def _disk_name_of(part_name):
    """sda1 → sda; mmcblk2p2 → mmcblk2; nvme0n1p3 → nvme0n1."""
    return re.sub(r"p?\d+$", "", part_name)


def parse_lsblk(data):
    """lsblk -J → список дисков с флагами system/mounted/operable.

    data — json-объект lsblk ({"blockdevices": [...]}); root_name — имя
    системного диска (без /dev/); если None — берётся из /proc/mounts.
    """
    root_name = root_disk_name()
    out = []
    for node in (data or {}).get("blockdevices") or []:
        if node.get("type") != "disk":
            continue
        name = node.get("name") or ""
        parts = []
        mounted = False
        for ch in node.get("children") or []:
            mp = ch.get("mountpoint") or (ch.get("mountpoints") or [None])[0]
            if mp:
                mounted = True
            parts.append({
                "name": ch.get("name") or "",
                "fstype": ch.get("fstype") or "",
                "label": ch.get("label") or "",
                "size": int(ch.get("size") or 0),
                "mountpoint": mp or "",
            })
        if node.get("mountpoint"):
            mounted = True
        system = bool(root_name and name == root_name)
        kind_ok = not (name.startswith("zram") or name.startswith("loop")
                       or name.startswith("fd") or name.startswith("sr")
                       or "boot" in name)
        operable = (not system) and (not mounted) and kind_ok \
            and bool(name) and int(node.get("size") or 0) > 0
        reason = ""
        if system:
            reason = "системный диск"
        elif mounted:
            reason = "есть смонтированные разделы"
        elif not kind_ok:
            reason = "внутренний/спец. носитель"
        elif int(node.get("size") or 0) <= 0:
            reason = "нулевой размер"
        out.append({
            "name": name,
            "size": int(node.get("size") or 0),
            "tran": node.get("tran") or "",
            "rm": bool(node.get("rm")),
            "model": (node.get("model") or "").strip(),
            "system": system,
            "mounted": mounted,
            "operable": operable,
            "reason": reason,
            "partitions": parts,
        })
    return out


def lsblk_disks():
    """Актуальный листинг: lsblk -J -b -o ..."""
    r = process.run([
        "lsblk", "-J", "-b", "-o",
        "NAME,PATH,TYPE,SIZE,TRAN,RM,MOUNTPOINT,MOUNTPOINTS,FSTYPE,LABEL,MODEL",
    ], timeout=15)
    return parse_lsblk(json.loads(r.stdout))


def find_disk(disks, name):
    for d in disks:
        if d["name"] == name:
            return d
    return None


def check_whole_disk(disks, name):
    """Guard для деструктивных операций на целом диске.

    Возвращает (True, "") либо (False, причина).
    """
    if not _DEV_RE.match(name or ""):
        return False, "недопустимое имя устройства"
    d = find_disk(disks, name)
    if d is None:
        return False, "диск не обнаружен"
    if not d["operable"]:
        return False, d["reason"] or "диск недоступен для операций"
    return True, ""


def check_partition(disks, part_name):
    """Guard для операций с разделом: раздел существует и не смонтирован."""
    if not _DEV_RE.match(part_name or ""):
        return False, "недопустимое имя устройства"
    for d in disks:
        if d["name"] == part_name:
            return False, "цель — раздел, а не целый диск"
        for p in d["partitions"]:
            if p["name"] == part_name:
                if p["mountpoint"]:
                    return False, "раздел смонтирован (%s)" % p["mountpoint"]
                if d["system"]:
                    return False, "системный диск"
                return True, ""
    return False, "раздел не обнаружен"


# --- SMART ---------------------------------------------------------------

def smart_report(dev_name):
    """smartctl -H -A -j → {ok, health, temp, attrs[], raw, error}."""
    path = dev_path(dev_name)
    r = process.run(["smartctl", "-H", "-A", "-j", path], timeout=45)
    raw = (r.stdout or "") + (r.stderr or "")
    try:
        data = json.loads(r.stdout)
    except Exception:
        if "smartctl" in raw and ("not found" in raw or "No such file" in raw
                                  or r.returncode == 127):
            return {"ok": False, "error": "smartctl не установлен"}
        return {"ok": False, "error": "smartctl не дал JSON",
                "raw": raw[-4000:]}
    health = {}
    st = data.get("smart_status") or {}
    health["passed"] = st.get("passed")
    temp = (data.get("temperature") or {}).get("current")
    attrs = []
    for a in (data.get("ata_smart_attributes") or {}).get("table") or []:
        attrs.append({
            "id": a.get("id"),
            "name": a.get("name"),
            "raw": (a.get("raw") or {}).get("value", ""),
            "worst": a.get("worst"),
            "thresh": a.get("thresh"),
            "failed": bool(a.get("failed")),
        })
    return {"ok": True, "health": health, "temp": temp, "attrs": attrs,
            "raw": raw[-4000:]}


# --- parted ---------------------------------------------------------------

def _b(val):
    """parted -m unit B: '1048576B' → 1048576."""
    s = str(val or "").rstrip("B")
    return int(s) if s.isdigit() else 0


def parse_parted(text):
    """parted -m print → {model, table, partitions:[{num,fs,start,end,size}]}"""
    parts = []
    table = ""
    model = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line == "BYT;":
            continue
        if line.endswith(";") and ":" in line and not line[0].isdigit():
            # /dev/sda:1000GB:1000GB:512B::;
            fields = line.rstrip(";").split(":")
            if fields[0].startswith("/dev/"):
                model = fields[0]
                table = fields[5] if len(fields) > 5 else ""
                continue
        if line[0].isdigit():
            # 1:1049kB:100MB:99MB:ext4::;
            fields = line.rstrip(";").split(":")
            if len(fields) < 5:
                continue
            try:
                num = int(fields[0])
            except (ValueError, IndexError):
                continue
            start, end, size = _b(fields[1]), _b(fields[2]), _b(fields[3])
            parts.append({
                "num": num,
                "fs": fields[4] if len(fields) > 4 else "",
                "start": start,
                "end": end,
                "size": size or max(0, end - start),
            })
    return {"model": model, "table": table, "partitions": parts}


def parted_print(dev_name):
    path = dev_path(dev_name)
    r = process.run(["parted", "-m", "-s", path, "unit", "B", "print"],
                    timeout=20)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()}
    return {"ok": True, **parse_parted(r.stdout)}


# --- операции -------------------------------------------------------------

def wipe_disk(dev_name):
    path = dev_path(dev_name)
    r = process.run(["wipefs", "-a", path], timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()}
    process.run(["blockdev", "--rereadpt", path], timeout=15)
    return {"ok": True}


def add_partition(dev_name, fs, start, end):
    """parted mkpart: start/end — целые байты или строки '100%'."""
    path = dev_path(dev_name)
    cmd = ["parted", "-s", path, "unit", "B", "mkpart", "primary"]
    if fs:
        cmd.append(fs)
    cmd += [str(int(start)), str(int(end))]
    r = process.run(cmd, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()}
    process.run(["blockdev", "--rereadpt", path], timeout=15)
    return {"ok": True}


def del_partition(dev_name, num):
    path = dev_path(dev_name)
    r = process.run(["parted", "-s", path, "rm", str(int(num))], timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()}
    process.run(["blockdev", "--rereadpt", path], timeout=15)
    return {"ok": True}


def format_partition(part_name, fs, label=""):
    """mkfs на разделе (раздел должен быть не смонтирован — guard снаружи)."""
    if fs not in _FS_TYPES:
        return {"ok": False, "error": "неподдерживаемая ФС: %r" % (fs,)}
    max_len = _LABEL_MAX.get(fs, 32)
    if label and len(label) > max_len:
        return {"ok": False, "error": "метка длиннее %d символов" % max_len}
    path = dev_path(part_name)
    if fs in ("ext2", "ext3", "ext4"):
        cmd = ["mkfs." + fs, "-F"]
        if label:
            cmd += ["-L", label]
    elif fs == "vfat":
        cmd = ["mkfs.vfat", "-F", "32"]
        if label:
            cmd += ["-n", label]
    elif fs == "ntfs":
        cmd = ["mkfs.ntfs", "-F"]
        if label:
            cmd += ["-L", label]
    elif fs == "exfat":
        cmd = ["mkfs.exfat"]
        if label:
            cmd += ["-n", label]
    else:
        cmd = ["mkfs." + fs]
        if label:
            cmd += ["-L", label]
    cmd.append(path)
    r = process.run(cmd, timeout=1800)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()}
    return {"ok": True}


def fsck_partition(part_name):
    """e2fsck -n (read-only) для ext*-разделов."""
    path = dev_path(part_name)
    r = process.run(["e2fsck", "-n", path], timeout=3600)
    # e2fsck: 0 — чисто; 1+ — ошибки (в т.ч. -n вернёт 4 и т.п.)
    out = (r.stdout or "") + (r.stderr or "")
    return {"ok": r.returncode == 0, "code": r.returncode, "raw": out[-4000:]}


# --- состояние долгих операций (клон / запись образа) ---------------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
        if st.get("running") and time.time() - st.get("started", 0) > 6 * 3600:
            st["running"] = False
            st["error"] = st.get("error") or "таймаут 6ч"
        return st
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or "/tmp", exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def update_state(**kw):
    st = load_state()
    st.update(kw)
    save_state(st)
    return st


def _io_written_bytes(pid):
    try:
        with open("/proc/%s/io" % pid, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("write_bytes:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


def _long_op_watcher(proc, total, on_progress, should_continue):
    """Прогресс dd/clone по write_bytes процесса (как core.storage)."""
    while proc.poll() is None:
        if should_continue and not should_continue():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return
        wb = _io_written_bytes(proc.pid)
        if wb is not None and total and on_progress:
            pct = min(99, int(wb * 100 / total))
            on_progress(pct, "записано %d из %d байт" % (wb, total))
        time.sleep(1.5)


def write_image(image_path, dev_name, on_progress=None, should_continue=None):
    """dd if=образ of=целый диск, конвейер прогресса как у клонирования."""
    total = os.path.getsize(image_path)
    src = dev_path(dev_name)
    proc = subprocess.Popen(
        ["dd", "if=" + image_path, "of=" + src, "bs=4M", "conv=fsync"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    t = threading.Thread(
        target=_long_op_watcher,
        args=(proc, total, on_progress, should_continue),
        daemon=True,
    )
    t.start()
    proc.wait()
    t.join(timeout=5)
    try:
        stderr = proc.stderr.read().decode("utf-8", "replace") \
            if proc.stderr else ""
    except Exception:
        stderr = ""
    process.run(["sync"], timeout=120)
    if proc.returncode != 0:
        return {"ok": False, "error": stderr[-2000:] or
                "dd exit %s" % proc.returncode}
    return {"ok": True}


# --- образы (FreeDOS + загруженные) ---------------------------------------

def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _cache_path(url):
    return os.path.join(IMAGE_DIR, re.sub(r"[^A-Za-z0-9._-]", "_",
                                          url.rsplit("/", 1)[-1] or "file.zip"))


def image_path_for(entry):
    """Пути: готовый .img (извлечён) и кэш-зип."""
    name = entry["key"] + "-" + entry["member"].rsplit("/", 1)[-1]
    img = os.path.join(IMAGE_DIR, re.sub(r"[^A-Za-z0-9._-]", "_", name))
    return img, _cache_path(entry["url"])


def list_images():
    """Каталог FreeDOS (скачан/нет) + загруженные пользователем файлы."""
    _ensure_dir(IMAGE_DIR)
    _ensure_dir(IMAGE_CUSTOM_DIR)
    out = []
    for e in FREEDOS_CATALOG:
        img, _ = image_path_for(e)
        out.append({**e, "ready": os.path.isfile(img),
                    "size": os.path.getsize(img) if os.path.isfile(img) else 0,
                    "source": "freedos"})
    for fn in sorted(os.listdir(IMAGE_CUSTOM_DIR)):
        p = os.path.join(IMAGE_CUSTOM_DIR, fn)
        if os.path.isfile(p):
            out.append({"key": "custom:" + fn, "title": fn,
                        "kind": "custom", "ready": True,
                        "size": os.path.getsize(p), "source": "custom",
                        "path": p})
    return out


def resolve_image(key):
    """Ключ образа → путь к файлу (или None)."""
    if key.startswith("custom:"):
        fn = key.split(":", 1)[1]
        if not re.match(r"^[A-Za-z0-9._-]{1,80}$", fn or ""):
            return None
        p = os.path.join(IMAGE_CUSTOM_DIR, fn)
        return p if os.path.isfile(p) else None
    for e in FREEDOS_CATALOG:
        if e["key"] == key:
            img, _ = image_path_for(e)
            return img if os.path.isfile(img) else None
    return None


def download_freedos(key, on_progress=None):
    """Скачивание/извлечение FreeDOS-образа (однократно, потом кэш)."""
    entry = next((e for e in FREEDOS_CATALOG if e["key"] == key), None)
    if entry is None:
        return {"ok": False, "error": "неизвестный образ"}
    _ensure_dir(IMAGE_DIR)
    img, zpath = image_path_for(entry)
    if os.path.isfile(img):
        return {"ok": True, "cached": True, "path": img}
    try:
        if not os.path.isfile(zpath):
            if on_progress:
                on_progress(0, "скачивание %s" % entry["url"])
            tmp = zpath + ".part"
            urllib.request.urlretrieve(entry["url"], tmp)
            os.replace(tmp, zpath)
        if on_progress:
            on_progress(80, "извлечение %s" % entry["member"])
        with zipfile.ZipFile(zpath) as z:
            with z.open(entry["member"]) as src, open(img + ".part", "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
        os.replace(img + ".part", img)
        if on_progress:
            on_progress(100, "готово")
        return {"ok": True, "path": img}
    except Exception as e:
        try:
            if os.path.isfile(img + ".part"):
                os.remove(img + ".part")
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def save_upload(filename, fileobj, max_bytes=700 * 1024 * 1024):
    """Сохранение пользовательского .img/.iso (валидация имени и размера)."""
    raw = filename or ""
    safe = os.path.basename(raw)
    if safe != raw or "/" in raw or "\\" in raw or ".." in raw:
        return {"ok": False, "error": "недопустимое имя файла"}
    if not re.match(r"^[A-Za-z0-9._-]{1,80}$", safe):
        return {"ok": False, "error": "недопустимое имя файла"}
    if not safe.lower().endswith((".img", ".ima", ".iso", ".bin")):
        return {"ok": False, "error": "нужен файл .img/.ima/.iso/.bin"}
    _ensure_dir(IMAGE_CUSTOM_DIR)
    path = os.path.join(IMAGE_CUSTOM_DIR, safe)
    written = 0
    try:
        with open(path, "wb") as f:
            while True:
                chunk = fileobj.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    f.close()
                    os.remove(path)
                    return {"ok": False, "error": "файл больше 700 МБ"}
                f.write(chunk)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if written == 0:
        os.remove(path)
        return {"ok": False, "error": "пустой файл"}
    return {"ok": True, "name": safe, "size": written}

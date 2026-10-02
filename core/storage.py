# -*- coding: utf-8 -*-
"""Storage layer — Storage Core (спека §7, PHASE 2.0-7).

Единая модель хранения, чтобы модули (downloads/torrent/dlna/backup/
camera/media/filemanager) не изобретали каждый свою (спека §7)::

    Storage API → /srv/media | /srv/data | /srv/backup

Состав:
- корни `ROOTS` + `path(root, *parts)` — единственный источник путей
  для модулей (миграция констант IPTV_DIR/MEDIA_DIR/PLAYLISTS_DIR —
  значения путей не менялись, см. журнал ROADMAP 2.0-7);
- backup targets — `DB_BACKUP_DIR`/`EMMC_BACKUP_PATH` (потребители:
  system_routes db/emmc, deploy/backup-db.sh, restore_server). Значения
  сохранены: их читают recovery.sh/restore_server/systemd-юниты, живущие
  за границей репо — переписывание путей без переноса данных отложено
  (честность: legacy вне /srv/backup);
- read-only контракты (PHASE 2.0-2): `lsblk_text/df_text/smart_report`
  (disks/partitions/mounts/SMART уже абстрагированы — structured-JSON API
  отложена до потребителя);
- `clone_disk()` — перенос do_clone (dd с прогрессом, Инвентаризация:
  2.0-7), потребитель — routes /api/system/clone-* в system_routes.

`shares`: management guest-шары уже в `core/samba_guest.py`; полный
list shares — по мере UI-потребителя (журнал 2.0-7).
"""
import os
import posixpath
import subprocess
import threading
import time

from core import process

# --- корни (спека §7) ------------------------------------------------------

ROOTS = {
    "media": "/srv/media",
    "data": "/srv/data",
    "backup": "/srv/backup",
}


def path(root, *parts):
    """Путь внутри корня Storage: ``path("media", "IPTV")``.

    Неизвестный корень — ValueError (ошибка программиста, не данных;
    прецедент: core.services.control на неверный action).
    """
    if root not in ROOTS:
        raise ValueError("неизвестный storage-корень: %r" % (root,))
    # posixpath: пути — POSIX (таргет Linux), локальный прогон на Windows
    return posixpath.join(ROOTS[root], *parts) if parts else ROOTS[root]


# --- backup targets (ROADMAP 2.0-7) ----------------------------------------

# Пишет deploy/backup-db.sh (systemd backup-db.service); читают
# system_routes.db_backup_list и restore_server. Legacy вне /srv/backup —
# держат recovery.sh/restore_server/systemd, см. docstring.
DB_BACKUP_DIR = "/srv/backup-db"

# Читает system_routes (backup-emmc.service пишет юнит вне репо) и
# restore_server; sys-emmc block.html — UI. Legacy вне /srv/backup.
EMMC_BACKUP_PATH = "/srv/backup-system/emmc.img.zst"


# --- read-only контракты (PHASE 2.0-2) -------------------------------------

def lsblk_text(timeout=10):
    """Вывод lsblk (дерево дисков/монтирований) как текст."""
    return process.run(
        ["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL"],
        timeout=timeout,
    ).stdout


def df_text(timeout=10):
    """Вывод df -h (заполненность ФС) как текст."""
    return process.run(["df", "-h"], timeout=timeout).stdout


def smart_report(device, timeout=10):
    """smartctl -a по /dev/<device>.

    Без устройства → "диск не обнаружен"; smartctl не запустился →
    "smartctl не установлен"; иначе — stdout, fallback stderr
    (контракт строгого api_disks сохранён байт-в-байт).
    """
    if not device:
        return "диск не обнаружен"
    dev = device if device.startswith("/dev/") else "/dev/" + device
    try:
        r = process.run(["smartctl", "-a", dev], timeout=timeout)
        return r.stdout or r.stderr
    except Exception:
        return "smartctl не установлен"


# --- клонирование диска (перенос do_clone, Инвентаризация 2.0-7) -----------

def _block_size_bytes(dev):
    """/sys/block/<имя>/size × 512 → байты; 0 — источник недоступен."""
    name = os.path.basename(dev or "")
    try:
        with open("/sys/block/%s/size" % name, "r", encoding="utf-8") as f:
            return int(f.read().strip()) * 512
    except Exception:
        return 0


def _clone_percent(rchar, total):
    """Прогресс как в 1.1: диапазон 5..95% по rchar от размера цели."""
    if not total:
        return 5
    return 5 + int(90 * min(rchar, total) / total)


def _clone_watcher(proc, total, on_progress, should_continue):
    """Watcher-тред: /proc/<pid>/io → on_progress(percent, text).

    Как в 1.1 (_dd_progress_watcher): опрос каждые 3 с; отмена снаружи
    (should_continue → False) останавливает прогресс, сам dd убивает
    внешний cancel-путь.
    """
    if not total or not on_progress:
        return
    while proc.poll() is None:
        if should_continue is not None and not should_continue():
            break
        try:
            rchar = 0
            with open("/proc/%d/io" % proc.pid, "r") as f:
                for line in f:
                    if line.startswith("rchar:"):
                        rchar = int(line.split()[1])
                        break
            percent = _clone_percent(rchar, total)
            on_progress(percent, "Копирование eMMC... %d%%" % percent)
        except Exception:
            pass
        time.sleep(3)


def clone_disk(src_dev, dst_dev, on_progress=None, should_continue=None,
               timeout=3600):
    """Клонирование диска dd с прогрессом → {ok, error} (не бросает).

    ``on_progress(percent, text)`` — вызывается из watcher-треда;
    ``should_continue()`` — cooperative-проверка (False → прогресс
    останавливается, dd продолжает до внешней отмены/завершения).
    Синхронизация (sync) до и после — внутри (как в 1.1 do_clone).
    """
    if not src_dev or not os.path.exists(src_dev):
        return {"ok": False, "error": "исходное устройство не обнаружено"}
    if not dst_dev or not os.path.exists(dst_dev):
        return {"ok": False, "error": "целевое устройство не обнаружено"}
    proc = None
    try:
        process.run(["sync"], timeout=10)
        if on_progress:
            on_progress(5, "Копирование eMMC...")
        total = _block_size_bytes(dst_dev)
        proc = subprocess.Popen(
            ["dd", "if=" + src_dev, "of=" + dst_dev, "bs=4M", "status=progress"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        watcher = threading.Thread(
            target=_clone_watcher,
            args=(proc, total, on_progress, should_continue),
            daemon=True,
        )
        watcher.start()
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"ok": False, "error": "таймаут клонирования (dd убит)"}
        watcher.join(timeout=10)
        if proc.returncode == 0:
            process.run(["sync"], timeout=30)
            return {"ok": True, "error": None}
        return {"ok": False,
                "error": (stderr or "").strip()[:200] or "Ошибка dd"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

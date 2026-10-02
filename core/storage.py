# -*- coding: utf-8 -*-
"""Storage layer — read-only контракт (PHASE 2.0-2).

Полный Storage Core (корни /srv/media|data|backup, shares, миграция
констант модулей) — PHASE 2.0-7, спека §6.

Потребитель (PHASE 2.0-2): modules/system_routes.api_disks
(роут GET /api/disks: lsblk + df + smartctl).
"""
from core import process


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
    (контракт старого api_disks сохранён байт-в-байт).
    """
    if not device:
        return "диск не обнаружен"
    dev = device if device.startswith("/dev/") else "/dev/" + device
    try:
        r = process.run(["smartctl", "-a", dev], timeout=timeout)
        return r.stdout or r.stderr
    except Exception:
        return "smartctl не установлен"

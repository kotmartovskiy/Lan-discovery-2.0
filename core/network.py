# -*- coding: utf-8 -*-
"""Network layer — read-only контракт (PHASE 2.0-2).

Письменные/транзакционные операции (addresses/routes/firewall, Prepare→
Apply→Verify→Commit) — PHASE 2.0-6 (Network Core), спека §5.

Потребитель (PHASE 2.0-2): core.capabilities._net_ifaces — убрано
дублирование sysfs-парсинга интерфейсов (1.1 держал его в capabilities).
"""
import os


def physical_ifaces(base="/sys/class/net"):
    """(wired, wireless) — списки имён физических интерфейсов из sysfs.

    Виртуальные (lo, veth, docker0, bridge — нет device/ и wireless/)
    пропускаются. None — источник недоступен (не-Linux / ошибка чтения):
    вызывающий обязан ответить unknown/unverified, а не absent.
    """
    if not os.path.isdir(base):
        return None
    try:
        names = os.listdir(base)
    except Exception:
        return None
    wired, wireless = [], []
    for n in sorted(names):
        if n == "lo":
            continue
        p = base + "/" + n
        is_wifi = os.path.isdir(p + "/wireless")
        if not is_wifi and not os.path.exists(p + "/device"):
            continue
        (wireless if is_wifi else wired).append(n)
    return wired, wireless

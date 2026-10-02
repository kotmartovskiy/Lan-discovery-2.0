# -*- coding: utf-8 -*-
"""Systemd services layer (PHASE 2.0-2): единый facade (спека §8).

Контракт (все ответы — словари, без исключений, кроме ValueError на
недопустимый action — это ошибка программиста, не транспортная):
    status(unit)  -> {"unit", "active", "enabled"}   (нет данных → "unknown")
    control(unit, action) -> {"ok": True, "unit", "action"}
                           | {"ok": False, "error"[, "timeout": True]}
    start/stop/restart/enable/disable(unit) — алиасы control()
    health(unit)  -> {"unit", "ok", "active", "enabled"}
    logs(unit, lines) -> текст журнала (ошибка → "")

Потребители (PHASE 2.0-2): modules/system_routes.py — статус служб в
/about и роут POST /api/service/<service>/<action>.
"""
import subprocess

from core import process

ACTIONS = ("start", "stop", "restart", "enable", "disable", "reload")
_ACTIVE_OK = ("active", "reloading", "activating")


def status(unit, timeout=5):
    """is-active + is-enabled одним вызовом слоя (как старый _check_service)."""
    active = process.out(["systemctl", "is-active", unit], timeout=timeout)
    enabled = process.out(["systemctl", "is-enabled", unit], timeout=timeout)
    return {
        "unit": unit,
        "active": active or "unknown",
        "enabled": enabled or "unknown",
    }


def control(unit, action, timeout=15):
    """Управление службой: start/stop/restart/enable/disable/reload."""
    if action not in ACTIONS:
        raise ValueError("недопустимое действие: %r" % (action,))
    try:
        r = process.run(["systemctl", action, unit], timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "timeout": True,
                "error": "Превышено время ожидания"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if r.returncode != 0:
        return {
            "ok": False,
            "error": (r.stderr or "").strip()
                     or "systemctl завершился с ошибкой",
        }
    return {"ok": True, "unit": unit, "action": action}


def start(unit, timeout=15):
    return control(unit, "start", timeout=timeout)


def stop(unit, timeout=15):
    return control(unit, "stop", timeout=timeout)


def restart(unit, timeout=15):
    return control(unit, "restart", timeout=timeout)


def enable(unit, timeout=15):
    return control(unit, "enable", timeout=timeout)


def disable(unit, timeout=15):
    return control(unit, "disable", timeout=timeout)


def health(unit, timeout=5):
    """ok = служба в рабочем состоянии (active/reloading/activating)."""
    st = status(unit, timeout=timeout)
    return dict(st, ok=st["active"] in _ACTIVE_OK)


def logs(unit, lines=100, timeout=5):
    """Последние строки журнала службы (journalctl -u, без пейджера)."""
    return process.out(
        ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short"],
        timeout=timeout,
    )

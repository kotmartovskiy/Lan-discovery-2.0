# -*- coding: utf-8 -*-
"""Hardware detection для Installer 2.0 (Спецификация §25).

Шаг «hardware detection» цепочки clean Debian → preflight → hardware
→ dependencies → core → modules → configuration → systemd → health.
Переиспользует core/hardware.detect_platform() (тот же источник, что
/api/health) — без Flask, только stdlib, работает ДО создания venv.

Не предполагает конкретную плату: все поля обрабатываются как
опциональные (отсутствие /sys, /proc/device-tree, /etc/os-release — норма).

Запуск:
    python3 tools/hw_detect.py               # отчёт в stdout
    python3 tools/hw_detect.py --out FILE    # отчёт в FILE + резюме
"""
import argparse
import json
import os
import platform
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.hardware import detect_platform  # noqa: E402 (stdlib-only модуль)


def os_release(path="/etc/os-release"):
    """Поля /etc/os-release (Debian/Ubuntu/Armbian); {} если файла нет."""
    data = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                data[key.strip()] = val.strip().strip('"\'')
    except OSError:
        pass
    return data


def build_report():
    """Отчёт детекта: платформа (единый источник — core.hardware) + дистрибутив."""
    distro = os_release()
    return {
        "detected_at": time.strftime("%d.%m.%Y %H:%M:%S"),
        "platform": detect_platform(),
        "distro": {
            "id": distro.get("ID", ""),
            "id_like": distro.get("ID_LIKE", ""),
            "name": distro.get("PRETTY_NAME", ""),
        },
        "python": platform.python_version(),
        "kernel": platform.release(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="LAN Discovery hw-detect (§25)")
    ap.add_argument("--out", help="куда записать hw-detect.json")
    args = ap.parse_args(argv)

    report = build_report()
    plat = report["platform"]
    summary = ("board=%s arch=%s emmc=%s sd=%s hdd=%s thermal=%s distro=%s"
               % (plat.get("board"), plat.get("arch"), plat.get("emmc"),
                  plat.get("sd"), plat.get("hdd"),
                  bool(plat.get("thermal_zone")),
                  report["distro"].get("id") or "?"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print("[install]   hw-detect: %s" % summary)
        print("[install]   hw-detect: отчёт -> %s" % args.out)
    else:
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

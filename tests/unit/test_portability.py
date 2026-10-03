# -*- coding: utf-8 -*-
"""Unit: PHASE 2.0-20 Portability (§30).

В коде панели нет плато-специфики: только capabilities и generic
Hardware Detection (hardware.py/capabilities.py). Имена плат и
вендоров допустимы в tests/ и tools/ (инвентарь репозитория),
но не в core/, modules/, templates/, static/, app.py, install.sh.
"""
import ipaddress
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

BOARD_NAMES = (
    "x96", "orangepi", "orange pi", "amlogic", "odroid", "rockchip",
    "rock pi", "raspberry", "raspberrypi", "bananapi", "nanopi",
    "pine64", "librecomput", "allwinner", "thinkpad", "tinker",
)

SCAN_DIRS = ("core", "modules", "templates", "static")
SCAN_FILES = ("app.py", "install.sh")
SCAN_SUFFIXES = (".py", ".html", ".js", ".css", ".json", ".sh", ".md")


def _scan_files():
    out = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.is_dir():
            out.extend(p for p in base.rglob("*")
                       if p.is_file() and p.suffix in SCAN_SUFFIXES)
    for f in SCAN_FILES:
        p = ROOT / f
        if p.is_file():
            out.append(p)
    return sorted(out)


def test_no_board_specific_names_in_panel_code():
    """§30: ни одного упоминания плат/вендоров в коде панели."""
    offenders = []
    for p in _scan_files():
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        for name in BOARD_NAMES:
            if name in text:
                offenders.append(
                    "%s: %r" % (p.relative_to(ROOT), name))
    assert not offenders, \
        "board-specific код (§30, заменить на capabilities): " \
        + "; ".join(offenders)


def test_sys_board_manifest_is_generic():
    """sys-board: имя/заголовок — generic; реальная плата из device-tree."""
    m = json.loads(
        (ROOT / "modules" / "sys-board" / "module.json")
        .read_text(encoding="utf-8"))
    blob = (m.get("name", "") + m.get("block", {}).get("title", "")).lower()
    assert m.get("name") and m.get("block", {}).get("title")
    for name in BOARD_NAMES:
        assert name not in blob, "манифест sys-board знает о плате: %s" % name


def test_help_lan_gated_by_subnet():
    """hf.lan — данные: показываются только внутри настроенной подсети."""
    from modules.core_routes import _help_facts

    hf = _help_facts()["hf"]
    try:
        inside = ipaddress.ip_address(hf["primary_ip"]) \
            in ipaddress.ip_network(hf["subnet"], strict=False)
    except ValueError:
        inside = False
    if inside:
        assert isinstance(hf["lan"], list) and hf["lan"]
        assert sum(1 for n in hf["lan"] if n["here"]) <= 1
    else:
        assert hf["lan"] == []


def test_app_sys_path_from_file_not_hardcoded():
    """B-05: sys.path app.py — от __file__, без хардкода /opt/lan-discovery.

    Иначе запуск с другим префиксом (--prefix) подхватывает чужое ядро 1.1.
    """
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "/opt/lan-discovery")' not in text
    assert "_APP_DIR = os.path.dirname(os.path.abspath(__file__))" in text



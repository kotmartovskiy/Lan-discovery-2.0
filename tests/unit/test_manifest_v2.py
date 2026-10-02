# -*- coding: utf-8 -*-
"""Unit: манифест 2.0 — сетка прав, валидация, версии, статусы, UI-запрос."""
import time

import pytest

import app
import modules.auth as auth
from core import manifest as mf
from core.module_loader import compute_status

CTX = {
    "arch": "aarch64",
    "caps": {
        "tools": {"nmap": {"state": "present"}},
        "storage": {"emmc": {"state": "present"}},
        "network": {"eth0": {"state": "present"}},
        "camera": {"usb0": {"state": "absent"}},
    },
    "app_version": "2.0.0",
}


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c


# ---------------- сетка прав ----------------

def test_grid_is_spec_11():
    expected = {
        "network.read", "network.configure", "storage.read", "storage.write",
        "services.read", "services.control", "process.execute",
        "camera.read", "camera.control", "usb.access", "gpio.access",
        "serial.access",
    }
    assert set(mf.PERMISSION_KEYS) == expected
    for key, label, desc in mf.PERMISSIONS_GRID:
        assert label and desc
        assert mf.permission_info(key) == (label, desc)


def test_permission_info_unknown():
    assert mf.permission_info("admin") is None


# ---------------- validate_manifest ----------------

def test_v1_manifest_still_valid():
    m = {"id": "downloads", "name": "Загрузки", "builtin": True,
         "deps": {"apt": [], "ports": []}, "permissions": []}
    assert mf.validate_manifest(m) == []


def test_validate_rejects_unknown_permission():
    errs = mf.validate_manifest({"id": "x", "permissions": ["network"]})
    assert any("неизвестное право" in e for e in errs)


def test_validate_rejects_bad_structure():
    assert mf.validate_manifest(None)
    assert any("id" in e for e in mf.validate_manifest({"id": "Bad Id!"}))
    assert any("permissions" in e
               for e in mf.validate_manifest({"id": "x", "permissions": "all"}))
    assert any("min_core_version" in e
               for e in mf.validate_manifest({"id": "x", "min_core_version": "v2"}))
    assert any("capabilities" in e
               for e in mf.validate_manifest({"id": "x", "capabilities": [1]}))
    assert any("capabilities" in e
               for e in mf.validate_manifest({"id": "x", "capabilities": ["A.B"]}))
    assert any("configuration" in e
               for e in mf.validate_manifest({"id": "x", "configuration": []}))


def test_validate_full_v2_manifest():
    m = {
        "id": "sdrcam", "name": "SDR камера", "version": "1.0.0",
        "publisher": "kotmartovskiy",
        "min_core_version": "2.0.0", "max_core_version": "3.0.0",
        "capabilities": ["network", "camera.usb0"],
        "dependencies": ["nettools"], "conflicts": ["other-cam"],
        "services": ["sdrd.service"], "configuration": {"api_key": "str"},
        "role_support": ["camera"],
        "permissions": ["camera.read", "camera.control", "network.read"],
        "hardware": {"arch": ["aarch64"]}, "deps": {"apt": ["rtl-sdr"]},
    }
    assert mf.validate_manifest(m) == []


# ---------------- версии ядра ----------------

def test_parse_version():
    assert mf.parse_version("1.2.3") == (1, 2, 3)
    assert mf.parse_version("2") == (2,)
    assert mf.parse_version("1.2.3.4") == (1, 2, 3, 4)
    assert mf.parse_version("v1.2") is None
    assert mf.parse_version(2) is None


def test_core_version_ok():
    assert mf.core_version_ok(None, None, "2.0.0")[0] is True
    assert mf.core_version_ok("", "", "2.0.0")[0] is True
    assert mf.core_version_ok("1.5.0", None, "2.0.0")[0] is True
    assert mf.core_version_ok("2.0.1", None, "2.0.0")[0] is False
    assert mf.core_version_ok(None, "1.9.0", "2.0.0")[0] is False
    assert mf.core_version_ok(None, "2.1.0", "2.0.0")[0] is True
    # подравнивание частей: 1.2 ~ 1.2.0
    assert mf.core_version_ok("1.2", None, "1.2.0")[0] is True
    # нечитаемая заявленная версия → fail-closed
    assert mf.core_version_ok("кто-то", None, "2.0.0")[0] is False
    # нечитаемый current → пропускаем (не гадаем)
    assert mf.core_version_ok("99.0.0", None, "weird")[0] is True


# ---------------- install_confirm_text (UI-запрос прав) ----------------

def test_confirm_text_empty_without_permissions():
    assert mf.install_confirm_text({"id": "x"}) == ""
    assert mf.install_confirm_text({}) == ""
    assert mf.install_confirm_text(None) == ""


def test_confirm_text_lists_rights():
    txt = mf.install_confirm_text(
        {"id": "sdrcam", "name": "Камера",
         "permissions": ["camera.read", "network.configure"]})
    assert "Камера" in txt
    assert "network.configure" in txt
    assert "Камеры: чтение" in txt
    assert "Сеть: настройка" in txt
    assert "Продолжить?" in txt


def test_confirm_text_shows_unknown_raw():
    txt = mf.install_confirm_text({"id": "x", "permissions": ["mystery"]})
    assert "mystery" in txt


# ---------------- compute_status (новые чеки манифеста 2.0) ----------------

def test_min_core_version_incompatible():
    m = {"id": "x", "min_core_version": "99.0.0"}
    assert compute_status(m, {}, CTX) == "incompatible"
    m_ok = {"id": "x", "min_core_version": "1.0.0"}
    assert compute_status(m_ok, {}, CTX) == "available"
    m_max = {"id": "x", "max_core_version": "1.9.0"}
    assert compute_status(m_max, {}, CTX) == "incompatible"


def test_min_core_version_skipped_without_app_version():
    ctx = dict(CTX)
    ctx.pop("app_version")
    m = {"id": "x", "min_core_version": "99.0.0"}
    assert compute_status(m, {}, ctx) == "available"


def test_priority_incompatible_over_missing_dep():
    m = {"id": "x", "min_core_version": "99.0.0",
         "dependencies": ["no-such-module"]}
    assert compute_status(m, {}, CTX) == "incompatible"


def test_capabilities_group_key():
    m_ok = {"id": "x", "capabilities": ["network.eth0"]}
    assert compute_status(m_ok, {}, CTX) == "available"
    m_bad = {"id": "x", "capabilities": ["camera.usb0"]}  # absent
    assert compute_status(m_bad, {}, CTX) == "requires-hardware"
    m_gone = {"id": "x", "capabilities": ["radio.thing"]}  # группы нет
    assert compute_status(m_gone, {}, CTX) == "requires-hardware"
    # голая группа не проверяем (не гадаем) — статус не портится
    m_bare = {"id": "x", "capabilities": ["network"]}
    assert compute_status(m_bare, {}, CTX) == "available"


def test_module_dependencies(monkeypatch, tmp_path):
    import core.module_loader as loader

    def fake_discover(force=False):
        return [{"id": "y", "builtin": True}, {"id": "z"}]

    monkeypatch.setattr(loader, "discover_modules", fake_discover)
    monkeypatch.setattr(loader, "load_state", lambda: {
        "z": {"installed": True, "enabled": True},
    })
    # зависимость есть и включена → не мешает
    m_ok = {"id": "x", "dependencies": ["y", "z"]}
    assert compute_status(m_ok, {}, CTX) == "available"
    # модуль-зависимость не установлен → requires-dependency
    m_missing = {"id": "x", "dependencies": ["nope"]}
    assert compute_status(m_missing, {}, CTX) == "requires-dependency"
    # установлен, но выключен → requires-dependency
    monkeypatch.setattr(loader, "load_state", lambda: {
        "z": {"installed": True, "enabled": False},
    })
    assert compute_status(m_ok, {}, CTX) == "requires-dependency"


# ---------------- UI: /modules показывает запрос прав ----------------

def _fake_modules(with_perms):
    m = {"id": "permtest", "name": "Perm", "description": "тест",
         "builtin": False, "deps": {"apt": ["x"]}}
    if with_perms:
        m["permissions"] = ["network.configure", "camera.read"]
    return m


def test_modules_page_confirm_request(client, monkeypatch):
    import json
    import re

    import core.module_loader as loader
    import modules.module_manager as mm

    mods = _fake_modules(with_perms=True)
    monkeypatch.setattr(loader, "discover_modules",
                        lambda force=False: [mods])
    monkeypatch.setattr(mm, "discover_modules",
                        lambda force=False: [mods])
    html = client.get("/modules").get_data(as_text=True)
    # confirm-текст вставляется через tojson (возможны \uXXXX) — декодируем
    m = re.search(r"onsubmit='return confirm\((.+?)\)'", html)
    assert m, "нет UI-запроса прав перед установкой"
    txt = json.loads(m.group(1))
    assert "запрашивает права" in txt
    assert "network.configure" in txt
    assert "camera.read" in txt
    # подписи прав в карточке модуля
    assert "Сеть: настройка (network.configure)" in html
    assert "Камеры: чтение (camera.read)" in html


def test_modules_page_no_confirm_without_permissions(client, monkeypatch):
    import core.module_loader as loader
    import modules.module_manager as mm

    mods = _fake_modules(with_perms=False)
    monkeypatch.setattr(loader, "discover_modules",
                        lambda force=False: [mods])
    monkeypatch.setattr(mm, "discover_modules",
                        lambda force=False: [mods])
    html = client.get("/modules").get_data(as_text=True)
    assert "onsubmit='return confirm(" not in html
    assert "запрашивает права" not in html

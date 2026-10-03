# -*- coding: utf-8 -*-
"""Unit: справка /help динамизирована (2026-10-01).

help.html больше не зашит под X96 Max: board/IP/диск/ssh/restore берутся
из _help_facts() (about_data + settings). Тест ловит возврат хардкода:
любая X96-константа, отсутствующая на текущем хосте, не должна попадать
в отрендеренную страницу.
"""
import os
import time

import pytest

import app
import modules.auth as auth


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


def test_help_renders_dynamic(client):
    r = client.get("/help")
    assert r.status_code == 200
    body = r.get_data(as_text=True)

    from modules.core_routes import _help_facts
    hf = _help_facts()["hf"]

    # динамические значения на месте
    assert hf["board"] in body
    assert hf["primary_ip"] in body
    assert hf["panel_url"] in body
    assert "ssh root@%s" % hf["primary_ip"] in body

    # старый X96-хардкод отсутствует, если текущий хост — не X96
    if hf["hostname"] != "armbian":
        assert "hostname <code>armbian</code>" not in body
    if hf["primary_ip"] != "192.168.3.243":
        assert "http://192.168.3.243:8080" not in body
        assert "ssh root@192.168.3.243" not in body


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="только Linux: диски /dev")
def test_help_facts_structure():
    from modules.core_routes import _help_facts
    hf = _help_facts()["hf"]
    assert hf["port"] == 8080
    assert hf["root_src"]
    assert hf["disks"] and all(
        d["dev"].startswith("/dev/") for d in hf["disks"]
    )
    # у каждого диска есть тип и назначение
    assert all(d["kind"] and d["role"] for d in hf["disks"])
    # маркер «эта панель» — не больше одной строки
    assert sum(1 for n in hf["lan"] if n["here"]) <= 1
    # включённые модули: builtin default True/True
    assert isinstance(hf["enabled"], set) and hf["enabled"]
    assert "weather" in hf["enabled"]


def test_help_module_sections_present_by_default(client):
    """Секция включённого модуля есть и в тексте, и в сайдбаре (help.md → mod-*)."""
    r = client.get("/help")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'id="mod-weather"' in body
    assert 'href="#mod-weather"' in body


def test_help_module_toggle_hides_section(client, monkeypatch):
    """Выключенный модуль исчезает из справки (секция, сайдбар, подсекции)."""
    import core.module_loader as ml

    orig = ml.module_status

    def fake(mid):
        if mid == "weather":
            return True, False
        return orig(mid)

    monkeypatch.setattr(ml, "module_status", fake)

    r = client.get("/help")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'id="mod-weather"' not in body
    assert 'href="#mod-weather"' not in body
    assert "Радиационный фон" not in body
    # ядро справки не задето
    assert 'id="hardware"' in body

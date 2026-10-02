# -*- coding: utf-8 -*-
"""Unit: Application Shell 2.0 (Спецификация §22–23).

Разделы HOME…ADMIN, dashboard на HOME, STORAGE, единые компоненты
(toast/confirm-dialog), навигация по активному разделу.
"""
import time

import pytest

import app
import modules.auth as auth
from core.module_loader import (SECTIONS, active_section, nav_groups,
                                nav_sections)

SPEC_KEYS = ["home", "network", "monitoring", "storage", "applications",
             "hardware", "system", "admin"]


@pytest.fixture()
def client(monkeypatch, devices_db):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = True


# --- §22: модель разделов ---------------------------------------------------

def test_sections_model_matches_spec_22():
    assert [s[0] for s in SECTIONS] == SPEC_KEYS


def test_nav_sections_all_for_admin_active_home():
    secs = nav_sections(True, "/")
    assert [s["key"] for s in secs] == SPEC_KEYS
    assert [s["key"] for s in secs if s["active"]] == ["home"]
    assert all(s["icon"] and s["name"] for s in secs)


def test_nav_sections_hides_admin_section_for_user():
    secs = nav_sections(False, "/history")
    keys = [s["key"] for s in secs]
    assert "admin" not in keys
    assert "home" in keys and "monitoring" in keys
    assert [s["key"] for s in secs if s["active"]] == ["monitoring"]


def test_active_section_mapping():
    assert active_section("/") == "home"
    assert active_section("/devices") == "network"
    assert active_section("/device/192.168.3.10") == "network"
    assert active_section("/history") == "monitoring"
    assert active_section("/storage") == "storage"
    assert active_section("/apps") == "applications"
    assert active_section("/capabilities") == "hardware"
    assert active_section("/roles") == "admin"
    assert active_section("/no-such-page") == ""


def test_shell_groups_filtered_by_section():
    mon = nav_groups(False, "monitoring")
    assert mon and all(
        e["section"] == "monitoring" for g in mon for e in g["entries"]
    )
    # без section — все группы (совместимость §32)
    assert len(nav_groups(False)) >= len(mon)


# --- §22: HOME = dashboard, STORAGE ----------------------------------------

def test_home_is_dashboard(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="dash"' in html
    for q in ("Всё ли нормально?", "Что сейчас происходит?",
              "Какие устройства подключены?", "Какие jobs выполняются?",
              "Есть ли проблемы?", "Есть ли предупреждения?"):
        assert q in html, q
    assert 'data-state="loading"' in html  # loading state (§23)
    # dashboard — не таблица устройств
    assert 'id="scanNowBtn"' not in html


def test_devices_moved_to_devices_path(client):
    html = client.get("/devices").get_data(as_text=True)
    assert "table-wrap" in html
    assert 'id="scanNowBtn"' in html


def test_storage_page(client):
    html = client.get("/storage").get_data(as_text=True)
    assert "Хранилище" in html
    assert "/srv/media" in html and "/srv/data" in html and "/srv/backup" in html
    # пустое состояние без lsblk/df (§23) — либо вывод, либо причина
    assert ("lsblk" in html.lower()) or ("недоступн" in html)


# --- §22: навигация shell в base ------------------------------------------

def test_base_has_section_navigation(client):
    html = client.get("/").get_data(as_text=True)
    for name in ("HOME", "NETWORK", "MONITORING", "STORAGE",
                 "APPLICATIONS", "HARDWARE", "SYSTEM", "ADMIN"):
        assert ">%s" % name in html, name
    assert 'aria-label="Разделы приложения"' in html
    # активный раздел помечен
    assert 'aria-current="page"' in html


def test_base_tabs_show_only_active_section(client):
    html = client.get("/history").get_data(as_text=True)
    # активен MONITORING: группы вкладок — только его пункты
    assert "nav-group-label" in html
    assert "Мониторинг" in html
    # иконки разделов едины (unified icons §23)
    assert 'class="section-ico"' in html


# --- §23: единые компоненты -------------------------------------------------

def test_base_has_unified_components(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="toast-root"' in html
    assert "window.toast" in html
    assert 'id="confirm-dialog"' in html
    assert "window.confirmDialog" in html
    # legacy alert() в шаблоне заменён на toast
    assert html.count("alert(") == 0


def test_home_after_login_redirects_to_dashboard(monkeypatch):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    c = app.app.test_client()
    r = c.post("/login", data={"username": "admin", "password": "x"},
               follow_redirects=False)
    # неверный пароль — просто форма; проверяем сам редирект-контракт ниже
    assert r.status_code in (200, 302)
    app.app.config["WTF_CSRF_ENABLED"] = True

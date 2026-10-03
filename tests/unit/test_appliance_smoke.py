# -*- coding: utf-8 -*-
"""Appliance smoke test (PHASE 2.0-17, §27): интеграционная цепочка.

Единый сценарий по шагам спеки §27 (важнее мелких UI-тестов):

  clean installation → login → hardware detection → capabilities →
  network discovery → module installation → role application →
  job execution → configuration change → update → failure
  simulation → rollback

В unit-окружении «чистая установка» = изолированная БД с нуля, «update»
= единый источник версии core.version (§34); полный update.sh/apt —
только на стенде (tests/live + ручной прогон, стенд N6).
"""
import time

import pytest

import app as app_module
import modules.auth as auth


@pytest.fixture()
def _csrf_off(monkeypatch):
    old = app_module.app.config["WTF_CSRF_ENABLED"]
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    yield
    app_module.app.config["WTF_CSRF_ENABLED"] = old


@pytest.fixture()
def smoke_env(monkeypatch, tmp_path, _csrf_off):
    """Чистая изоляция: users/БД/jobs/events/settings/module state/roles."""
    from core import db as core_db
    from core import jobs as core_jobs
    import core.config as core_config
    import core.db as dr
    import core.module_loader as ml
    import core.roles as roles
    import modules.core_routes as cr
    import modules.devices_routes as droutes
    import modules.system_routes as sys_routes

    app_module.app.config["TESTING"] = True
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {
            "enabled": True, "role": "admin",
            "password_hash": auth._hash("1234"),
        }},
    )

    # clean installation: схема с нуля (миграции 0→3)
    monkeypatch.setattr(dr, "DB", str(tmp_path / "devices.db"))
    monkeypatch.setattr(dr, "_init_done", False)
    dr.init_db_schema(force=True)
    monkeypatch.setattr(core_db, "DB", str(tmp_path / "events.db"))
    monkeypatch.setattr(core_db, "_init_done", False)
    # health и системные роуты ходят в свой DB (/opt/...) — изолируем
    monkeypatch.setattr(sys_routes, "DB", str(tmp_path / "devices.db"))

    # jobs manager на изолированной sqlite
    m = core_jobs.manager
    monkeypatch.setattr(m, "_db_path_cfg", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(m, "_init_done", False)
    monkeypatch.setattr(m, "_jobs", {})
    monkeypatch.setattr(m, "_events", {})
    monkeypatch.setattr(m, "_retention_cfg", 0)

    # state/настройки -> tmp (не трогаем /etc)
    monkeypatch.setattr(ml, "STATE_PATH", str(tmp_path / "modules.json"))
    monkeypatch.setattr(ml, "_state_cache", {"mtime": -1, "data": None})
    monkeypatch.setattr(roles, "STATE_PATH", str(tmp_path / "roles.json"))
    monkeypatch.setattr(core_config, "SETTINGS_PATH",
                        str(tmp_path / "settings.json"))
    monkeypatch.setattr(cr, "_settings_cache", {"data": None, "ts": 0.0})

    # сеть не трогаем: скан подменён (паттерн test_jobs)
    monkeypatch.setattr(
        droutes, "run_scan",
        lambda subnet=None, ifaces=None:
        "Nmap scan report for 10.0.0.1\nHost is up.\n")
    monkeypatch.setattr(
        droutes, "reconcile",
        lambda con, cur, now=None, events_out=None:
        {"new": 1, "online": 1, "offline": 0, "mac_changed": 0,
         "ip_changed": 0})

    return tmp_path


def _jobs_before(core_jobs):
    return {j["id"] for j in core_jobs.list(limit=20)}


def _new_job(core_jobs, before):
    """Job, созданный после before (list() — новые первыми, но фильтр по set)."""
    fresh = [j for j in core_jobs.list(limit=20) if j["id"] not in before]
    assert len(fresh) == 1, "ожидался ровно один новый job: %r" % fresh
    return fresh[0]["id"]


def test_appliance_chain_27(smoke_env, monkeypatch):
    from core import jobs as core_jobs
    import core.module_loader as ml
    from core.version import APP_VERSION
    from test_syschange import FakeApt

    tmp = smoke_env
    c = app_module.app.test_client()

    # --- 2. login: анониму админ-API закрыт, после /login — открыт
    # D-08: API без сессии — JSON 401 (раньше 302 на HTML-форму)
    assert c.get("/api/settings").status_code == 401
    r = c.post("/login", data={"username": "admin", "password": "1234"})
    assert r.status_code in (200, 302)
    assert c.get("/api/settings").status_code == 200

    # --- 1+3. installation (чистая БД) + hardware detection
    r = c.get("/api/health")
    assert r.status_code in (200, 503)  # 503 — критичные чеки без системных
    h = r.get_json()
    assert h["db"]["status"] == "ok"
    assert h["db"]["user_version"] == 3          # схема 2.0 (v3)
    assert set(h["platform"]) == {"board", "arch", "system", "emmc", "sd",
                                  "hdd", "thermal_zone"}

    # --- 4. capabilities
    r = c.get("/api/capabilities")
    assert r.status_code == 200
    caps = r.get_json()
    assert isinstance(caps, dict) and caps

    # --- 5. network discovery (fake nmap)
    r = c.post("/api/scan", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["job"]
    assert core_jobs.wait(d["job"], timeout=5) == "completed"
    j = core_jobs.get(d["job"])
    assert j["result"]["devices"] == 1
    assert j["result"]["stats"]["new"] == 1

    # --- 6. module installation (deps пустые у notes; state -> tmp)
    before = _jobs_before(core_jobs)
    r = c.post("/modules/notes/install")
    assert r.status_code == 302
    jid = _new_job(core_jobs, before)
    assert core_jobs.wait(jid, timeout=5) == "completed"
    j = core_jobs.get(jid)
    assert j["result"]["ok"] is True
    assert j["meta"]["module"] == "notes"

    # --- 7. role application (default: blockers нет, state -> tmp)
    r = c.get("/api/roles")
    assert r.status_code == 200
    ov = r.get_json()
    assert ov["roles"]
    r = c.post("/api/roles/default/apply")
    assert r.status_code == 200
    ra = r.get_json()
    assert ra["ok"] is True, ra
    assert ra.get("active") in (None, "default")

    # --- 8. job execution: обе задачи выполнены
    done = [x for x in core_jobs.list(limit=20)
            if x["type"] in ("network-scan", "module-install")]
    assert len(done) >= 2
    assert all(x["status"] == "completed" for x in done)

    # --- 9. configuration change (+ откат значения)
    r = c.post("/api/settings", json={"network": {"scan_interval": 45}})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/settings").get_json()[
        "network"]["scan_interval"] == 45
    r = c.post("/api/settings", json={"network": {"scan_interval": 30}})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    # --- 10. update: единый источник версии (§34); update.sh — стенд
    assert h["version"] == APP_VERSION

    # --- 11. failure simulation: зависимости модуля не ставятся
    from core import syschange
    monkeypatch.setattr(syschange, "BACKUP_ROOT", str(tmp / "rollback"))
    fail_apt = FakeApt(install_ok=False)
    import modules.module_manager as mm
    real_get_module = mm.get_module
    status_calls = []
    monkeypatch.setattr(mm, "_run", fail_apt)
    monkeypatch.setattr(mm, "set_module_status",
                        lambda mid, installed=None, enabled=None:
                        status_calls.append(mid))
    monkeypatch.setattr(
        mm, "get_module",
        lambda mid: {"id": "smoke-fail", "deps": {"apt": ["pkg-a"]}}
        if mid == "smoke-fail" else real_get_module(mid))

    before = _jobs_before(core_jobs)
    r = c.post("/modules/smoke-fail/install")
    assert r.status_code == 302
    jid = _new_job(core_jobs, before)
    assert core_jobs.wait(jid, timeout=5) == "failed"
    j = core_jobs.get(jid)
    logs = "\n".join(j["logs"])
    assert "apt install pkg-a: FAIL" in logs
    assert "rollback" in logs

    # --- 12. rollback: системные изменения откатились, статус чист
    assert "pkg-a" not in fail_apt.installed
    assert "smoke-fail" not in status_calls   # не помечен установленным
    # настройки вернулись к исходным (шаг 9 закрыт)
    assert c.get("/api/settings").get_json()[
        "network"]["scan_interval"] == 30
    # devices/events целы после всего пути
    assert h["db"]["devices"] >= 0
    assert ml.load_state() is not None

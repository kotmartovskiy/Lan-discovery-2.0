# -*- coding: utf-8 -*-
"""Unit: PHASE 2.0-3 — core/jobs (менеджер, отмена, retention, recover)
+ роуты /api/jobs + миграция install/scan/db-backup на jobs."""
import sqlite3
import subprocess
import time

import pytest

import app
import modules.auth as auth
from core import jobs as core_jobs


@pytest.fixture(autouse=True)
def _csrf_off():
    """POST-контрактные тесты без CSRF-токена (паттерн test_roles)."""
    old = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = False
    yield
    app.app.config["WTF_CSRF_ENABLED"] = old


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


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    """Глобальный manager на изолированной sqlite + чистое состояние.

    PHASE 2.0-12: события job.* пишутся в core.db.DB — патчим и его,
    чтобы emit() шёл в tmp, а не в /opt (Windows-окружение).
    """
    from core import db as core_db
    m = core_jobs.manager
    db = str(tmp_path / "jobs.db")
    monkeypatch.setattr(m, "_db_path_cfg", db)
    monkeypatch.setattr(m, "_init_done", False)
    monkeypatch.setattr(m, "_jobs", {})
    monkeypatch.setattr(m, "_events", {})
    monkeypatch.setattr(m, "_retention_cfg", 0)
    monkeypatch.setattr(core_db, "DB", str(tmp_path / "events.db"))
    monkeypatch.setattr(core_db, "_init_done", False)
    return db


# --- manager: lifecycle -----------------------------------------------------

def test_submit_completed_lifecycle(jobs_db):
    def job(ctx):
        ctx.log("step-1")
        ctx.progress(50)
        ctx.log("step-2")
        return {"v": 1}

    jid = core_jobs.submit("t", job)
    assert core_jobs.wait(jid, timeout=5) == "completed"
    j = core_jobs.get(jid)
    assert j["status"] == "completed"
    assert j["progress"] == 100
    assert j["result"] == {"v": 1}
    assert j["error"] is None
    assert j["started_at"] and j["finished_at"]
    assert any("step-1" in line for line in j["logs"])
    for key in ("id", "type", "status", "progress", "queued_at",
                "started_at", "finished_at", "logs", "result", "error",
                "cancelable"):
        assert key in j, key


def test_submit_failed_records_error(jobs_db):
    def boom(ctx):
        raise RuntimeError("oops")

    jid = core_jobs.submit("t", boom)
    assert core_jobs.wait(jid, timeout=5) == "failed"
    j = core_jobs.get(jid)
    assert "oops" in j["error"]
    assert any("ERROR" in line for line in j["logs"])
    assert j["finished_at"]


def test_job_persisted_to_sqlite(jobs_db):
    jid = core_jobs.submit("t", lambda ctx: {"ok": True})
    assert core_jobs.wait(jid, timeout=5) == "completed"
    con = sqlite3.connect(jobs_db)
    row = con.execute(
        "SELECT status, progress, logs, result, cancelable FROM jobs "
        "WHERE id=?", (jid,)).fetchone()
    con.close()
    assert row[0] == "completed"
    assert row[1] == 100
    assert '"ok"' in row[3]
    assert row[4] == 0


def test_list_merges_db_history_and_memory(jobs_db):
    jid = core_jobs.submit("t", lambda ctx: 1)
    assert core_jobs.wait(jid, timeout=5) == "completed"

    # история видна даже новым менеджером (как после рестарта панели)
    other = core_jobs.JobManager(db_path=jobs_db, workers=0)
    items = other.list()
    assert [j["id"] for j in items][0] == jid
    assert other.list(status="completed")
    assert other.list(status="running") == []


# --- отмена -----------------------------------------------------------------

def test_cancel_queued_never_runs(tmp_path):
    m = core_jobs.JobManager(db_path=str(tmp_path / "q.db"), workers=0)
    ran = []
    jid = m.submit("t", lambda ctx: ran.append(1))
    res = m.cancel(jid)
    assert res["ok"] is True
    assert res["status"] == "cancelled"
    assert m.get(jid)["status"] == "cancelled"
    # даже когда воркер подхватит id — статус уже не queued → skip
    m._run_one(jid)
    assert ran == []


def test_cancel_running_cooperative(jobs_db):
    def long_job(ctx):
        for _ in range(300):
            ctx.check_cancel()
            time.sleep(0.02)
        return "never"

    jid = core_jobs.submit("t", long_job, cancelable=True)
    for _ in range(300):
        if core_jobs.get(jid)["status"] == "running":
            break
        time.sleep(0.01)
    res = core_jobs.cancel(jid)
    assert res["ok"] is True
    assert res.get("cancelling") is True
    assert core_jobs.wait(jid, timeout=5) == "cancelled"


def test_cancel_not_cancelable_running(jobs_db):
    jid = core_jobs.submit("t", lambda ctx: time.sleep(0.4) or 1,
                           cancelable=False)
    for _ in range(300):
        if core_jobs.get(jid)["status"] == "running":
            break
        time.sleep(0.01)
    res = core_jobs.cancel(jid)
    assert res["ok"] is False
    assert core_jobs.wait(jid, timeout=5) == "completed"


def test_cancel_terminal_and_unknown(jobs_db):
    jid = core_jobs.submit("t", lambda ctx: 1)
    assert core_jobs.wait(jid, timeout=5) == "completed"
    res = core_jobs.cancel(jid)
    assert res["ok"] is False
    assert core_jobs.cancel("no-such-job") is None


# --- recover / retention ----------------------------------------------------

def test_recover_interrupted_marks_failed(jobs_db):
    con = sqlite3.connect(jobs_db)
    core_jobs.ensure_jobs_table(con)
    con.execute(
        "INSERT INTO jobs (id, type, status, queued_at, started_at, "
        "cancelable) VALUES ('old-run', 't', 'running', ?, ?, 0)",
        (core_jobs.now_ts(), core_jobs.now_ts()))
    con.commit()
    con.close()

    m = core_jobs.JobManager(db_path=jobs_db, workers=0)
    m.ensure_schema()
    con = sqlite3.connect(jobs_db)
    row = con.execute(
        "SELECT status, error FROM jobs WHERE id='old-run'").fetchone()
    con.close()
    assert row[0] == "failed"
    assert "перезапуском" in row[1]


def test_cleanup_old_jobs_retention(jobs_db):
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=400)).strftime(
        "%d.%m.%Y %H:%M:%S")
    fresh = core_jobs.now_ts()
    con = sqlite3.connect(jobs_db)
    core_jobs.ensure_jobs_table(con)
    for jid, ts in (("j-old", old), ("j-new", fresh)):
        con.execute(
            "INSERT INTO jobs (id, type, status, queued_at, cancelable) "
            "VALUES (?, 't', 'completed', ?, 0)", (jid, ts))
    con.commit()

    assert core_jobs.cleanup_old_jobs(con, 0) == 0  # retention выключен
    assert core_jobs.cleanup_old_jobs(con, 180) == 1
    rows = [r[0] for r in con.execute("SELECT id FROM jobs")]
    con.close()
    assert rows == ["j-new"]


# --- роуты ------------------------------------------------------------------

def test_api_jobs_list_shape(client, jobs_db):
    jid = core_jobs.submit("t", lambda ctx: {"x": 1})
    assert core_jobs.wait(jid, timeout=5) == "completed"
    r = client.get("/api/jobs")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert isinstance(d["active"], int)
    assert d["jobs"][0]["id"] == jid
    for key in ("type", "status", "progress", "logs", "cancelable"):
        assert key in d["jobs"][0], key


def test_api_jobs_get_and_404(client, jobs_db):
    jid = core_jobs.submit("t", lambda ctx: 1)
    assert core_jobs.wait(jid, timeout=5) == "completed"
    r = client.get("/api/jobs/%s" % jid)
    assert r.status_code == 200
    assert r.get_json()["job"]["status"] == "completed"
    assert client.get("/api/jobs/nope").status_code == 404


def test_api_jobs_bad_status_filter(client, jobs_db):
    assert client.get("/api/jobs?status=garbage").status_code == 400


def test_api_jobs_cancel_route(client, jobs_db):
    def long_job(ctx):
        for _ in range(300):
            ctx.check_cancel()
            time.sleep(0.02)

    jid = core_jobs.submit("t", long_job, cancelable=True)
    for _ in range(300):
        if core_jobs.get(jid)["status"] == "running":
            break
        time.sleep(0.01)
    r = client.post("/api/jobs/%s/cancel" % jid)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert core_jobs.wait(jid, timeout=5) == "cancelled"
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_api_jobs_auth_required(jobs_db):
    c = app.app.test_client()  # без сессии
    assert c.get("/api/jobs").status_code in (302, 401, 403)


# --- миграция: module install ----------------------------------------------

def test_install_route_submits_job(client, jobs_db, monkeypatch):
    import modules.module_manager as mm

    monkeypatch.setattr(mm, "get_module",
                        lambda mid: {"id": mid, "deps": {}})
    monkeypatch.setattr(mm, "module_status", lambda mid: (False, False))
    rec, st = [], []
    monkeypatch.setattr(mm, "record_install_result",
                        lambda mid, result: rec.append((mid, result)))
    monkeypatch.setattr(mm, "set_module_status",
                        lambda mid, **kw: st.append((mid, kw)))

    r = client.post("/modules/test-mod/install")
    assert r.status_code == 302
    assert "ok=" in r.headers.get("Location", "")

    items = [j for j in core_jobs.list(limit=10)
             if j["type"] == "module-install"]
    assert items
    jid = items[0]["id"]
    assert core_jobs.wait(jid, timeout=5) == "completed"
    j = core_jobs.get(jid)
    assert j["meta"] == {"module": "test-mod"}
    assert rec and rec[0][0] == "test-mod"
    assert st == [("test-mod", {"installed": True, "enabled": True})]


def test_install_job_failed_deps(client, jobs_db, monkeypatch):
    import modules.module_manager as mm

    monkeypatch.setattr(mm, "get_module",
                        lambda mid: {"id": mid, "deps": {"apt": ["xpkg"]}})
    monkeypatch.setattr(mm, "module_status", lambda mid: (False, False))
    monkeypatch.setattr(mm, "_run",
                        lambda cmd, timeout=600: (False, "apt err"))
    rec = []
    monkeypatch.setattr(mm, "record_install_result",
                        lambda mid, result: rec.append(mid))
    monkeypatch.setattr(mm, "set_module_status", lambda mid, **kw: None)

    assert client.post("/modules/bad-mod/install").status_code == 302
    items = [j for j in core_jobs.list(limit=10)
             if j["type"] == "module-install"]
    assert core_jobs.wait(items[0]["id"], timeout=5) == "failed"
    # состояние модуля зафиксировано до падения — как в синхронном коде
    assert rec == ["bad-mod"]
    assert "зависимости" in core_jobs.get(items[0]["id"])["error"]


def test_catalog_install_route_submits_job(client, jobs_db, monkeypatch):
    import modules.module_manager as mm

    calls = []
    monkeypatch.setattr(mm, "install_module",
                        lambda mid, update=False: calls.append((mid, update)))

    r = client.post("/modules/cat-mod/catalog/install")
    assert r.status_code == 302
    items = [j for j in core_jobs.list(limit=10)
             if j["type"] == "module-catalog-install"]
    assert items
    assert core_jobs.wait(items[0]["id"], timeout=5) == "completed"
    assert calls == [("cat-mod", False)]


def test_install_manifest_ctx_contract(jobs_db, tmp_path, monkeypatch):
    """_install_manifest с ctx: логи шагов + финальный прогресс."""
    import modules.module_manager as mm

    monkeypatch.setattr(mm, "_run",
                        lambda cmd, timeout=600: (True, ""))
    d = str(tmp_path / "ctx-dir")
    jid = core_jobs.submit(
        "t",
        lambda ctx: mm._install_manifest(
            {"id": "m", "deps": {"dirs": [d]}}, ctx=ctx))
    assert core_jobs.wait(jid, timeout=5) == "completed"
    j = core_jobs.get(jid)
    assert j["result"]["ok"] is True
    assert j["progress"] == 100
    assert any("mkdir" in line for line in j["logs"])


# --- миграция: network scan -------------------------------------------------

def test_scan_route_submits_job(client, jobs_db, devices_db, monkeypatch):
    import modules.devices_routes as dr

    monkeypatch.setattr(
        dr, "run_scan",
        lambda subnet=None, ifaces=None:
        "Nmap scan report for 10.0.0.1\nHost is up.\n")
    monkeypatch.setattr(
        dr, "reconcile",
        lambda con, cur, now=None:
        {"new": 1, "online": 1, "offline": 0, "mac_changed": 0,
         "ip_changed": 0})

    r = client.post("/api/scan", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["job"]

    assert core_jobs.wait(d["job"], timeout=5) == "completed"
    j = core_jobs.get(d["job"])
    assert j["type"] == "network-scan"
    assert j["result"]["devices"] == 1
    assert j["result"]["stats"]["new"] == 1


def test_scan_route_validation_still_sync(client, jobs_db):
    r = client.post("/api/scan", json={"ifaces": ["", 5]})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


# --- миграция: db-backup ----------------------------------------------------

def test_db_backup_route_submits_job(client, jobs_db, monkeypatch):
    import modules.system_routes as sr

    monkeypatch.setattr(
        sr, "_db_backup_job",
        lambda ctx: (ctx.log("fake"), {"ok": True})[-1])

    r = client.post("/system/db-backup")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["job"]
    assert core_jobs.wait(d["job"], timeout=5) == "completed"


def test_db_backup_job_waits_systemd_unit(jobs_db, monkeypatch):
    import core.process as core_process
    import modules.system_routes as sr

    seen = []
    monkeypatch.setattr(
        core_process, "run",
        lambda cmd, timeout=30, **kw: (
            seen.append(cmd),
            subprocess.CompletedProcess(cmd, 0, "done", ""))[-1])

    jid = core_jobs.submit("db-backup", sr._db_backup_job)
    assert core_jobs.wait(jid, timeout=5) == "completed"
    assert seen == [["systemctl", "start", "backup-db.service"]]
    assert core_jobs.get(jid)["result"]["returncode"] == 0


def test_db_backup_job_failure(jobs_db, monkeypatch):
    import core.process as core_process
    import modules.system_routes as sr

    monkeypatch.setattr(
        core_process, "run",
        lambda cmd, timeout=30, **kw:
        subprocess.CompletedProcess(cmd, 1, "", "unit failed"))

    jid = core_jobs.submit("db-backup", sr._db_backup_job)
    assert core_jobs.wait(jid, timeout=5) == "failed"
    assert "rc=1" in core_jobs.get(jid)["error"]


# --- UI-виджет --------------------------------------------------------------

def test_widget_markup_present():
    import os
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    html = open(os.path.join(root, "templates", "base.html"),
                encoding="utf-8").read()
    assert 'id="jobs-widget"' in html
    assert "/api/jobs?limit=" in html
    assert "data-job-cancel" in html


# --- события §16 (PHASE 2.0-12): потребитель job.* ------------------------

def test_job_emits_namespace_events(jobs_db):
    """job.started/completed пишутся в events (namespace §16)."""
    from core import db as core_db

    jid = core_jobs.submit("t-ns", lambda ctx: {"ok": 1})
    assert core_jobs.wait(jid, timeout=5) == "completed"
    con = sqlite3.connect(core_db.DB)
    try:
        rows = [r[0] for r in con.execute(
            "SELECT event FROM events ORDER BY id")]
        meta = con.execute(
            "SELECT metadata FROM events WHERE event='job.completed'"
        ).fetchone()[0]
    finally:
        con.close()
    assert rows == ["job.started", "job.completed"]
    assert '"job_type": "t-ns"' in meta


def test_job_failed_emits_critical(jobs_db):
    from core import db as core_db

    def boom(ctx):
        raise RuntimeError("boom")

    jid = core_jobs.submit("t-fail", boom)
    assert core_jobs.wait(jid, timeout=5) == "failed"
    con = sqlite3.connect(core_db.DB)
    try:
        rows = {r[0]: r[1] for r in con.execute(
            "SELECT event, severity FROM events ORDER BY id")}
    finally:
        con.close()
    assert rows.get("job.failed") == "critical"
    assert "job.completed" not in rows


def test_job_emit_subscriber(jobs_db):
    """Фундамент Automation (§17): подписчик видит жизненный цикл."""
    from core import events as core_events

    seen = []
    unsub = core_events.subscribe(lambda p: seen.append(p["name"]))
    try:
        jid = core_jobs.submit("t-sub", lambda ctx: None)
        assert core_jobs.wait(jid, timeout=5) == "completed"
    finally:
        unsub()
    assert "job.started" in seen
    assert "job.completed" in seen

# -*- coding: utf-8 -*-
"""Unit: PHASE 2.0-2 — core/process, services, config, network, storage
(read-only контракты + первый перенос вызовов из system_routes)."""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

import pytest

import app
import modules.auth as auth
from core import config as core_config
from core import network as core_network
from core import process as core_process
from core import services as core_services
from core import storage as core_storage


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


# --- core.process ---------------------------------------------------------

def test_process_run_text():
    r = core_process.run(
        [sys.executable, "-c", "print('p202-ok')"], timeout=15)
    assert r.returncode == 0
    assert "p202-ok" in r.stdout


def test_process_out_strips_and_swallows_errors():
    assert core_process.out(
        [sys.executable, "-c", "print('  spaced  ')"], timeout=15) == "spaced"
    assert core_process.out(["__no_such_binary__"], timeout=5) == ""


def test_process_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        core_process.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.5)


def test_process_shell_string():
    r = core_process.run("echo shell-ok", timeout=10)
    assert r.returncode == 0
    assert "shell-ok" in r.stdout


# --- core.services --------------------------------------------------------

def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        ["systemctl"], returncode, stdout, stderr)


def test_services_status(monkeypatch):
    def fake_out(cmd, timeout=5, **kw):
        return "active" if "is-active" in cmd else "enabled"

    monkeypatch.setattr(core_services.process, "out", fake_out)
    st = core_services.status("lan-discovery")
    assert st == {"unit": "lan-discovery", "active": "active",
                  "enabled": "enabled"}


def test_services_status_unknown(monkeypatch):
    monkeypatch.setattr(core_services.process, "out",
                        lambda cmd, timeout=5, **kw: "")
    st = core_services.status("no-such.service")
    assert st["active"] == "unknown"
    assert st["enabled"] == "unknown"


def test_services_control_ok(monkeypatch):
    seen = []

    def fake_run(cmd, timeout=30, **kw):
        seen.append(cmd)
        return _cp(0)

    monkeypatch.setattr(core_services.process, "run", fake_run)
    res = core_services.control("transmission-daemon", "restart")
    assert res == {"ok": True, "unit": "transmission-daemon",
                   "action": "restart"}
    assert seen == [["systemctl", "restart", "transmission-daemon"]]


def test_services_control_error_passthrough(monkeypatch):
    monkeypatch.setattr(core_services.process, "run",
                        lambda cmd, timeout=30, **kw: _cp(1, "", "no uid"))
    res = core_services.control("x.service", "stop")
    assert res == {"ok": False, "error": "no uid"}


def test_services_control_timeout(monkeypatch):
    def fake_run(cmd, timeout=30, **kw):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(core_services.process, "run", fake_run)
    res = core_services.control("x.service", "start")
    assert res["ok"] is False
    assert res["timeout"] is True
    assert "ожидания" in res["error"]


def test_services_control_bad_action():
    with pytest.raises(ValueError):
        core_services.control("x.service", "format-disk")


def test_services_health(monkeypatch):
    monkeypatch.setattr(
        core_services.process, "out",
        lambda cmd, timeout=5, **kw: "active" if "is-active" in cmd
        else "enabled")
    h = core_services.health("ssh")
    assert h["ok"] is True
    assert h["active"] == "active"

    monkeypatch.setattr(
        core_services.process, "out",
        lambda cmd, timeout=5, **kw: "inactive" if "is-active" in cmd
        else "disabled")
    assert core_services.health("ssh")["ok"] is False


def test_services_logs(monkeypatch):
    monkeypatch.setattr(core_services.process, "out",
                        lambda cmd, timeout=5, **kw: "line1\nline2")
    assert core_services.logs("ssh", lines=10) == "line1\nline2"


def test_service_action_route_shape(client, monkeypatch):
    """Роут /api/service/<...>/action: JSON-контракт 1.1 сохранён."""
    monkeypatch.setattr(core_services.process, "run",
                        lambda cmd, timeout=30, **kw: _cp(1, "", "denied"))
    r = client.post("/api/service/transmission/restart")
    assert r.status_code in (500, 504)
    d = r.get_json()
    assert d["ok"] is False
    assert "error" in d

    monkeypatch.setattr(core_services.process, "run",
                        lambda cmd, timeout=30, **kw: _cp(0))
    r = client.post("/api/service/transmission/restart")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "service": "transmission-daemon",
                            "action": "restart"}

    r = client.post("/api/service/transmission/nonsense")
    assert r.status_code == 400


def test_service_action_route_auth_required():
    c = app.app.test_client()  # без сессии
    r = c.post("/api/service/transmission/restart")
    assert r.status_code in (302, 401, 403)


# --- core.config ----------------------------------------------------------

def test_config_roundtrip(tmp_path):
    p = str(tmp_path / "settings.json")
    core_config.clear_cache(p)
    assert core_config.load(p) == {}
    data = {"web": {"flask_port": 8080}}
    assert core_config.save(data, p) is True
    assert core_config.load(p) == data
    assert core_config.get("web", "flask_port", path=p) == 8080
    assert core_config.get("web", "nope", "dflt", path=p) == "dflt"
    assert core_config.get("no-such", "k", "dflt", path=p) == "dflt"


def test_config_non_dict_section(tmp_path):
    p = str(tmp_path / "s.json")
    core_config.save({"web": 5}, p)
    assert core_config.get("web", "port", "dflt", path=p) == "dflt"


def test_config_corrupt_and_list(tmp_path):
    p1 = tmp_path / "bad.json"
    p1.write_text("{oops", encoding="utf-8")
    core_config.clear_cache(str(p1))
    assert core_config.load(str(p1)) == {}

    p2 = tmp_path / "list.json"
    p2.write_text("[1, 2]", encoding="utf-8")
    core_config.clear_cache(str(p2))
    assert core_config.load(str(p2)) == {}


def test_system_routes_settings_delegation(monkeypatch):
    """modules/system_routes.load_settings → core.config.load (единый кэш)."""
    import modules.system_routes as sr

    monkeypatch.setattr(
        core_config, "load",
        lambda path=None: {"__path__": path})
    assert sr.load_settings() == {"__path__": sr.SETTINGS_PATH}


# --- core.network ---------------------------------------------------------

def test_network_physical_ifaces_tree(tmp_path):
    base = tmp_path / "net"
    (base / "eth0").mkdir(parents=True)
    (base / "eth0" / "device").touch()
    (base / "wlan0" / "wireless").mkdir(parents=True)
    (base / "lo").mkdir()
    (base / "docker0").mkdir()
    assert core_network.physical_ifaces(str(base)) == (
        ["eth0"], ["wlan0"])


def test_network_physical_ifaces_source_missing(tmp_path):
    assert core_network.physical_ifaces(str(tmp_path / "nope")) is None


def test_capabilities_delegates_to_network_contract(monkeypatch):
    from core import capabilities as caps_mod

    monkeypatch.setattr(
        core_network, "physical_ifaces",
        lambda base="/sys/class/net": (["eth9"], []))
    assert caps_mod._net_ifaces() == (["eth9"], [])


# --- core.storage ---------------------------------------------------------

def test_storage_lsblk_df_via_process(monkeypatch):
    seen = []

    class R:
        stdout = "NAME SIZE\n"

    def fake_run(cmd, timeout=30, **kw):
        seen.append(cmd)
        return R()

    monkeypatch.setattr(core_storage.process, "run", fake_run)
    assert core_storage.lsblk_text() == "NAME SIZE\n"
    assert core_storage.df_text() == "NAME SIZE\n"
    assert seen[0][0] == "lsblk"
    assert seen[1][:2] == ["df", "-h"]


def test_storage_smart_report_contract(monkeypatch):
    assert core_storage.smart_report(None) == "диск не обнаружен"

    seen = []

    class R:
        stdout = "TEMP 40C"

    def fake_run(cmd, timeout=30, **kw):
        seen.append(cmd)
        return R()

    monkeypatch.setattr(core_storage.process, "run", fake_run)
    assert core_storage.smart_report("sda") == "TEMP 40C"
    assert seen == [["smartctl", "-a", "/dev/sda"]]

    def boom(cmd, timeout=30, **kw):
        raise FileNotFoundError("smartctl")

    monkeypatch.setattr(core_storage.process, "run", boom)
    assert core_storage.smart_report("sda") == "smartctl не установлен"


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="только Linux: lsblk/диски")
def test_api_disks_shape(client):
    """GET /api/disks после переноса на core.storage: старый контракт."""
    r = client.get("/api/disks")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    for key in ("lsblk", "df", "smart"):
        assert key in d, key
    assert isinstance(d["lsblk"], str)
    assert isinstance(d["df"], str)


def test_device_identity_api(client, tmp_path, monkeypatch):
    """2.0-11 §15: GET /api/device/<ip>/identity — аддитивный read API."""
    import core.db as core_db

    monkeypatch.setattr(core_db, "DB", str(tmp_path / "devices.db"))
    monkeypatch.setattr(core_db, "_init_done", False)
    core_db.init_db_schema(force=True)
    con = sqlite3.connect(core_db.DB)
    con.execute(
        "INSERT INTO devices (ip, online, mac, first_seen, last_seen, "
        "appearances, misses) VALUES ('192.168.3.50', 1, "
        "'AA:BB:CC:DD:EE:10', 't1', 't2', 1, 0)")
    con.execute(
        "INSERT INTO ip_history (device_id, ip, first_seen, last_seen) "
        "VALUES ('mac:aa:bb:cc:dd:ee:10', '192.168.3.50', 't1', 't2')")
    con.commit()
    con.close()

    r = client.get("/api/device/192.168.3.50/identity")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["device_id"] == "mac:aa:bb:cc:dd:ee:10"
    assert data["ip"] == "192.168.3.50"
    assert data["mac"] == "AA:BB:CC:DD:EE:10"
    assert data["ip_history"] == [
        {"ip": "192.168.3.50", "first_seen": "t1", "last_seen": "t2"}]
    assert client.get(
        "/api/device/10.255.255.254/identity").status_code == 404


# --- Task1: единый кэш/writer настроек + атомарная запись core.config -------

def test_config_torn_write_keeps_old_file(tmp_path):
    """Сбой сериализации не портит файл: цель цела, tmp-обломков нет."""
    p = str(tmp_path / "settings.json")
    core_config.clear_cache(p)
    assert core_config.save({"web": {"flask_port": 8080}}, p) is True

    class _NotJson:
        pass

    with pytest.raises(TypeError):
        core_config.save({"web": {"port": _NotJson()}}, p)
    # цель не тронута: на диске по-прежнему старый валидный JSON
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"web": {"flask_port": 8080}}
    assert not os.path.exists(p + ".tmp")
    core_config.clear_cache(p)
    assert core_config.load(p) == {"web": {"flask_port": 8080}}


def test_single_settings_writer_invariant():
    """§20/Task1: кэш и запись settings — только core/config.py.

    Вторая независимая реализация (кэш + open(..., "w")) в app/modules/core
    — ровно то, что породило неатомарный /api/settings и рассинхрон кэшей.
    """
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))  # tests/unit -> корень
    cache_hits, write_hits = [], []
    files = [os.path.join(root, "app.py")]
    files += sorted(glob.glob(os.path.join(root, "core", "*.py")))
    files += sorted(glob.glob(os.path.join(root, "modules", "*.py")))
    for path in files:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel == "core/config.py":
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if "_settings_cache" in text:
            cache_hits.append(rel)
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r'open\([^)]*settings[^)]*,\s*["\']w', line, re.I):
                write_hits.append("%s:%d" % (rel, n))
    assert cache_hits == [], "второй кэш settings: %s" % cache_hits
    assert write_hits == [], "неатомарная запись settings: %s" % write_hits


# --- Task4: префикс/БД без хардкода /opt ------------------------------------

def test_prefix_env_and_db_path(tmp_path, monkeypatch):
    """LAN_PREFIX переопределяет префикс; core.db следует за core.config."""
    import importlib
    import core.db as cdb

    monkeypatch.setenv("LAN_PREFIX", str(tmp_path))
    try:
        importlib.reload(core_config)
        assert core_config.PREFIX == str(tmp_path)
        assert core_config.DB_PATH == str(tmp_path / "devices.db")
    finally:
        monkeypatch.delenv("LAN_PREFIX", raising=False)
        importlib.reload(core_config)
    # без env — каталог самого core/config.py (<repo>/core → <repo>):
    # дефолт идентичен старому /opt и не зависит от префикса
    repo = os.path.dirname(
        os.path.dirname(os.path.abspath(core_config.__file__)))
    assert core_config.PREFIX == repo
    assert core_config.DB_PATH == os.path.join(repo, "devices.db")
    # core.db.DB вычислен при импорте того же config — рассинхрон невозможен
    assert cdb.DB == core_config.DB_PATH

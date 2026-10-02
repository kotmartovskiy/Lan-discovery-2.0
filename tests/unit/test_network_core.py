# -*- coding: utf-8 -*-
"""Unit: Network Core — объектный API, транзакции, операторы (PHASE 2.0-6).

Спека §5–6: Prepare→Apply→Verify→Commit/Rollback, валидация входа,
авто-rollback (insurance), перенос wifi-scan и контракты роутов.
"""
import time

import pytest

import app
import modules.auth as auth
from core import network as cn


# --- read-only объекты -----------------------------------------------------

def test_list_interfaces_classifies_and_none_source(monkeypatch):
    data = [
        {"ifname": "lo", "operstate": "unknown", "address": "00:00:00:00:00:00", "mtu": 65536},
        {"ifname": "eth0", "operstate": "up", "address": "aa:bb:cc:dd:ee:01", "mtu": 1500},
        {"ifname": "wlan0", "operstate": "down", "address": "aa:bb:cc:dd:ee:02", "mtu": 1500},
        {"ifname": "docker0", "operstate": "down", "address": "02:42:00:00:00:01", "mtu": 1500},
    ]
    monkeypatch.setattr(cn, "_ip_json", lambda args, timeout=10: data)
    monkeypatch.setattr(cn, "physical_ifaces", lambda base="/sys/class/net": (["eth0"], ["wlan0"]))
    out = cn.list_interfaces()
    by_name = {i["name"]: i for i in out}
    assert by_name["lo"]["type"] == "loopback"
    assert by_name["eth0"]["type"] == "wired" and by_name["eth0"]["up"] is True
    assert by_name["wlan0"]["type"] == "wifi" and by_name["wlan0"]["up"] is False
    assert by_name["docker0"]["type"] == "virtual"
    assert by_name["eth0"]["mac"] == "aa:bb:cc:dd:ee:01" and by_name["eth0"]["mtu"] == 1500

    monkeypatch.setattr(cn, "_ip_json", lambda args, timeout=10: None)
    assert cn.list_interfaces() is None


def test_list_addresses_and_routes_shape(monkeypatch):
    addrs = [{"ifname": "eth0", "addr_info": [
        {"family": "inet", "local": "192.168.3.10", "prefixlen": 24,
         "scope": "global", "dynamic": True},
        {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"},
    ]}]
    routes = [{"dst": "default", "gateway": "192.168.3.1", "dev": "eth0",
               "metric": 100, "protocol": "dhcp", "table": "main"}]
    monkeypatch.setattr(cn, "_ip_json",
                        lambda args, timeout=10: addrs if args[:1] == ["addr"] else routes)
    a = cn.list_addresses()
    assert a[0] == {"iface": "eth0", "family": "inet", "addr": "192.168.3.10",
                    "prefixlen": 24, "scope": "global", "dynamic": True}
    assert a[1]["family"] == "inet6" and a[1]["dynamic"] is False
    r = cn.list_routes()
    assert r[0]["dst"] == "default" and r[0]["gateway"] == "192.168.3.1"

    monkeypatch.setattr(cn, "_ip_json", lambda args, timeout=10: None)
    assert cn.list_addresses() is None and cn.list_routes() is None


# --- wifi-scan (перенос парсера) -------------------------------------------

IW_SAMPLE = """\
BSS aa:bb:cc:dd:ee:ff(on wlan1)
        TSF: 123 usec (0 d, 0 u sec)
        freq: 2412
        beacon interval: 100 TU
        capability: ESS Privacy (0x0011)
        signal: -55.00 dBm
        SSID: MyNet
        RSN:     * Version: 1
                 * Group cipher: CCMP
BSS 11:22:33:44:55:66(on wlan1)
        freq: 5180
        signal: -70.00 dBm
        SSID: FiveG
        WPA:     * Version: 1
        secondary channel above
        VHT: oper channel 1
BSS 22:33:44:55:66:77(on wlan1)
        freq: 2484
        signal: -80.00 dBm
        SSID:
"""


def test_parse_iw_scan_fields_and_sort():
    nets = cn._parse_iw_scan(IW_SAMPLE)
    assert [n["ssid"] for n in nets] == ["MyNet", "FiveG"]  # скрытая не попала, сортировка
    m = nets[0]
    assert m["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert m["channel"] == 1 and m["frequency"] == 2412
    assert m["signal"] == -55.0 and m["signal_pct"] == 90
    assert m["encryption"] == "WPA2" and m["bandwidth"] == "20MHz"
    g = nets[1]
    assert g["channel"] == 36 and g["encryption"] == "WPA"
    # elif-цепочка как в 1.1: поздняя строка VHT перекрывает secondary-40MHz
    assert g["bandwidth"] == "80MHz+"


def test_wifi_scan_contract(monkeypatch):
    class R:
        returncode = 0
        stdout = IW_SAMPLE
        stderr = ""
    monkeypatch.setattr(cn.process, "run", lambda cmd, timeout=15: R())
    res = cn.wifi_scan(["wlan1"])
    assert res["ok"] is True and len(res["networks"]) == 2

    class Bad:
        returncode = 1
        stdout = ""
        stderr = "No such device"
    monkeypatch.setattr(cn.process, "run", lambda cmd, timeout=15: Bad())
    res = cn.wifi_scan(["wlan9"])
    assert res == {"ok": True, "networks": []}  # как в 1.1: iw отработал без результата

    def _boom(cmd, timeout=15):
        raise FileNotFoundError("iw")
    monkeypatch.setattr(cn.process, "run", _boom)
    res = cn.wifi_scan(["wlan9"])
    assert res["ok"] is False and "iw" in res["error"]


# --- валидация входа -------------------------------------------------------

def test_input_validation_helpers():
    assert cn._valid_iface("eth0") and cn._valid_iface("enp0s3")
    assert not cn._valid_iface("eth0; rm -rf /")
    assert not cn._valid_iface("a" * 16) and not cn._valid_iface("-x")
    assert not cn._valid_iface(None)

    assert cn._parse_cidr("192.168.3.5/24").ip.__str__() == "192.168.3.5"
    assert cn._parse_cidr("fe80::1/64") is not None
    assert cn._parse_cidr("192.168.3.5") is None
    assert cn._parse_cidr("2001:db8::/64 extra") is None
    assert cn._parse_cidr(None) is None

    assert cn._parse_dst("default") == "default"
    assert cn._parse_dst("192.168.3.0/24") == "192.168.3.0/24"
    assert cn._parse_dst("10.0.0.1") == "10.0.0.1/32"  # host route нормализуется
    assert cn._parse_dst("evil dst") is None and cn._parse_dst("") is None


# --- транзакционный каркас -------------------------------------------------

def test_transaction_happy_path_order_and_commit():
    calls = []
    tx = cn.Transaction("happy")
    tx.add("one", apply=lambda: calls.append("a1"), prepare=lambda: calls.append("p1") or "d1",
           verify=lambda: calls.append("v1") or True)
    tx.add("two", apply=lambda: calls.append("a2"), verify=lambda: True,
           rollback=lambda d: calls.append(("rb", d)))
    res = tx.run()
    assert res["ok"] is True and res["phase"] == "commit" and res["rolled_back"] is False
    assert calls[:4] == ["p1", "a1", "v1", "a2"]
    assert [s["state"] for s in res["steps"]] == ["verified", "verified"]


def test_transaction_prepare_fail_no_apply():
    calls = []
    tx = cn.Transaction("prep")
    tx.add("one", apply=lambda: calls.append("apply"), prepare=lambda: False)
    tx.add("two", apply=lambda: calls.append("apply2"))
    res = tx.run()
    assert res["ok"] is False and res["phase"] == "prepare"
    assert res["rolled_back"] is False and calls == []  # ничего не применялось
    assert res["steps"][0]["state"] == "aborted"


def test_transaction_prepare_exception():
    def _boom():
        raise RuntimeError("net down")
    tx = cn.Transaction("prep-ex")
    tx.add("one", apply=lambda: None, prepare=_boom)
    res = tx.run()
    assert res["ok"] is False and res["phase"] == "prepare"
    assert "net down" in res["error"]


def test_transaction_apply_fail_rolls_back_reverse():
    calls = []
    tx = cn.Transaction("apply-fail")
    tx.add("first", apply=lambda: None, prepare=lambda: "d1",
           rollback=lambda d: calls.append(("rb1", d)))
    tx.add("second", apply=lambda: False, prepare=lambda: "d2",
           rollback=lambda d: calls.append(("rb2", d)))
    tx.add("third", apply=lambda: calls.append("NEVER"), prepare=lambda: "d3")
    res = tx.run()
    assert res["ok"] is False and res["phase"] == "apply"
    assert res["rolled_back"] is True
    assert calls == [("rb2", "d2"), ("rb1", "d1")]  # обратный порядок
    assert "NEVER" not in calls
    # prepare готовит все шаги заранее, apply третьего не было
    assert res["steps"][2]["state"] == "prepared"


def test_transaction_verify_fail_rolls_back():
    calls = []
    tx = cn.Transaction("verify-fail")
    tx.add("only", apply=lambda: None, prepare=lambda: "snap",
           verify=lambda: False, rollback=lambda d: calls.append(d))
    res = tx.run()
    assert res["ok"] is False and res["phase"] == "verify"
    assert res["rolled_back"] is True and calls == ["snap"]


def test_transaction_apply_exception_maps_to_error():
    def _boom():
        raise OSError("ip: command failed")
    tx = cn.Transaction("ex")
    tx.add("one", apply=_boom, prepare=lambda: None, rollback=lambda d: None)
    res = tx.run()
    assert res["ok"] is False and res["phase"] == "apply"
    assert "ip: command failed" in res["error"]
    assert res["rolled_back"] is True


def test_transaction_missing_and_failing_rollback():
    tx = cn.Transaction("no-rb")
    tx.add("one", apply=lambda: False, prepare=lambda: "d")
    res = tx.run()
    assert res["rolled_back"] is False  # шаг без rollback — честно о неполноте
    assert res["steps"][0]["state"] == "no-rollback"

    tx2 = cn.Transaction("bad-rb")
    tx2.add("one", apply=lambda: False, prepare=lambda: "d",
            rollback=lambda d: (_ for _ in ()).throw(RuntimeError("rb err")))
    res2 = tx2.run()
    assert res2["ok"] is False and res2["rolled_back"] is False
    assert "rb err" in res2["steps"][0]["error"]


def test_transaction_empty():
    res = cn.Transaction("empty").run()
    assert res["ok"] is False and res["phase"] == "prepare"


# --- insurance (авто-rollback, спека §6) ------------------------------------

def _ok_tx():
    tx = cn.Transaction("ins")
    tx.add("one", apply=lambda: None, prepare=lambda: "snap", rollback=lambda d: None)
    assert tx.run()["ok"]
    return tx


def test_insurance_check_fail_triggers_rollback():
    calls = []
    tx = cn.Transaction("ins-fail")
    tx.add("one", apply=lambda: None, prepare=lambda: "snap",
           rollback=lambda d: calls.append(d))
    assert tx.run()["ok"]
    assert tx.schedule_rollback(lambda: False, grace=0.02) is True
    assert tx.insurance_done.wait(2)
    assert tx.insurance["ok"] is False
    assert tx.insurance["rolled_back"] is True
    assert tx.insurance["rollback_ok"] is True and calls == ["snap"]


def test_insurance_check_ok_no_rollback():
    calls = []
    tx = cn.Transaction("ins-ok")
    tx.add("one", apply=lambda: None, prepare=lambda: "snap",
           rollback=lambda d: calls.append(d))
    assert tx.run()["ok"]
    assert tx.schedule_rollback(lambda: True, grace=0.01) is True
    assert tx.insurance_done.wait(2)
    assert tx.insurance["ok"] is True
    assert tx.insurance["rolled_back"] is False and calls == []


def test_insurance_disabled_cases():
    assert _ok_tx().schedule_rollback(lambda: False, grace=0) is False
    assert _ok_tx().schedule_rollback("not callable", grace=1) is False
    tx = cn.Transaction("not-run")
    tx.add("one", apply=lambda: None, prepare=lambda: None)
    assert tx.schedule_rollback(lambda: False, grace=1) is False  # run() не вызывался


# --- операторы -------------------------------------------------------------

def test_set_address_validation_before_transaction(monkeypatch):
    reads = []
    monkeypatch.setattr(cn, "_addr_snapshot", lambda i: reads.append(i) or None)
    res = cn.set_address("eth0; reboot", "192.168.3.5/24")
    assert res["ok"] is False and res["phase"] is None
    res = cn.set_address("eth0", "192.168.3.5")
    assert res["ok"] is False and res["phase"] is None
    assert reads == []  # валидация ДО чтения состояния
    # источник недоступен → phase None, транзакция не начиналась
    res = cn.set_address("eth0", "192.168.3.5/24")
    assert res["ok"] is False and res["phase"] is None
    assert reads == ["eth0"]


def test_set_address_happy(monkeypatch):
    monkeypatch.setattr(cn, "_addr_snapshot", lambda i: [{"local": "10.0.0.1", "prefixlen": 24}])
    monkeypatch.setattr(cn, "_ip_rc", lambda args, timeout=10: True)
    monkeypatch.setattr(cn, "_addr_present", lambda i, ip, plen: True)
    res = cn.set_address("eth0", "192.168.3.5/24")
    assert res["ok"] is True and res["phase"] == "commit"


def test_set_address_verify_fail_rolls_back_snapshot(monkeypatch):
    restored = []
    monkeypatch.setattr(cn, "_addr_snapshot", lambda i: [{"local": "10.0.0.1", "prefixlen": 24}])
    monkeypatch.setattr(cn, "_ip_rc", lambda args, timeout=10: True)
    monkeypatch.setattr(cn, "_addr_present", lambda i, ip, plen: False)  # verify падает
    monkeypatch.setattr(cn, "_restore_addrs", lambda i, snap: restored.append((i, snap)) or True)
    res = cn.set_address("eth0", "192.168.3.5/24")
    assert res["ok"] is False and res["phase"] == "verify"
    assert res["rolled_back"] is True
    assert restored == [("eth0", [{"local": "10.0.0.1", "prefixlen": 24}])]


def test_del_address_calls_del_and_restores(monkeypatch):
    calls = []
    monkeypatch.setattr(cn, "_addr_snapshot", lambda i: [{"local": "10.0.0.1", "prefixlen": 24}])
    monkeypatch.setattr(cn, "_ip_rc", lambda args, timeout=10: calls.append(list(args)) or True)
    monkeypatch.setattr(cn, "_addr_present", lambda i, ip, plen: False)  # адреса больше нет
    res = cn.del_address("eth0", "192.168.3.5/24")
    assert res["ok"] is True
    assert calls[0] == ["addr", "del", "192.168.3.5/24", "dev", "eth0"]


def test_set_route_requires_target_and_validates(monkeypatch):
    monkeypatch.setattr(cn, "_routes_json", lambda dst: pytest.fail("не должен читаться"))
    res = cn.set_route("default")
    assert res["ok"] is False and res["phase"] is None and "gateway" in res["error"]
    res = cn.set_route("evil dst", gateway="192.168.3.1")
    assert res["ok"] is False and res["phase"] is None
    res = cn.set_route("default", gateway="not-an-ip")
    assert res["ok"] is False and res["phase"] is None


def test_set_route_happy_and_restore(monkeypatch):
    snap = [{"dst": "default", "gateway": "192.168.3.1", "dev": "eth0", "metric": None}]
    after = [{"dst": "default", "gateway": "192.168.3.9", "dev": "eth0", "metric": None}]
    n = {"i": 0}

    def _fake_routes(dst):
        n["i"] += 1
        return snap if n["i"] == 1 else after  # prepare → снимок, verify → после apply

    monkeypatch.setattr(cn, "_routes_json", _fake_routes)
    monkeypatch.setattr(cn, "_ip_rc", lambda args, timeout=10: True)
    restored = []
    monkeypatch.setattr(cn, "_restore_routes", lambda d, s: restored.append(s) or True)
    res = cn.set_route("default", gateway="192.168.3.9")
    assert res["ok"] is True and res["phase"] == "commit"

    # verify падает (маршрут не появился) → откат к снимку
    monkeypatch.setattr(cn, "_routes_json", lambda dst: snap)
    res2 = cn.set_route("default", gateway="192.168.3.9")
    assert res2["ok"] is False and res2["phase"] == "verify"
    assert res2["rolled_back"] is True and restored[-1] is snap


def test_del_route_happy(monkeypatch):
    calls = []
    n = {"i": 0}
    entry = [{"dst": "10.9.0.0/16", "gateway": "10.9.0.1", "dev": "eth0", "metric": None}]

    def _fake_routes(dst):
        n["i"] += 1
        return entry if n["i"] == 1 else []  # prepare → есть, verify → удалён

    monkeypatch.setattr(cn, "_routes_json", _fake_routes)
    monkeypatch.setattr(cn, "_ip_rc", lambda args, timeout=10: calls.append(list(args)) or True)
    res = cn.del_route("10.9.0.0/16")
    assert res["ok"] is True
    assert calls[0] == ["route", "del", "10.9.0.0/16"]


# --- роуты -----------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    app.app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"},
                 "guest": {"enabled": True, "role": "guest"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = True


@pytest.fixture()
def guest_client(client):
    with client.session_transaction() as s:
        s["user"] = "guest"
    yield client
    with client.session_transaction() as s:
        s["user"] = "admin"


def test_route_info_contract(client, monkeypatch):
    monkeypatch.setattr(cn, "list_interfaces",
                        lambda base="/sys/class/net": [{"name": "eth0", "type": "wired",
                                                       "state": "up", "up": True,
                                                       "mac": "", "mtu": 1500}])
    monkeypatch.setattr(cn, "list_addresses", lambda: [{"iface": "eth0", "family": "inet",
                                                        "addr": "192.168.3.243",
                                                        "prefixlen": 24, "scope": "global",
                                                        "dynamic": False}])
    monkeypatch.setattr(cn, "list_routes", lambda: [{"dst": "default",
                                                     "gateway": "192.168.3.1",
                                                     "dev": "eth0", "metric": None,
                                                     "protocol": "dhcp", "table": "main"}])
    r = client.get("/api/network/info")
    d = r.get_json()
    assert r.status_code == 200 and d["ok"] is True
    assert d["interfaces"][0]["name"] == "eth0"
    assert d["routes"][0]["dst"] == "default"

    monkeypatch.setattr(cn, "list_interfaces", lambda base="/sys/class/net": None)
    d = client.get("/api/network/info").get_json()
    assert d["ok"] is False and "прочитать" in d["error"]


def test_route_address_admin_and_validation(client, guest_client, monkeypatch):
    # guest → 403
    r = guest_client.post("/api/network/address",
                          json={"action": "set", "iface": "eth0", "cidr": "10.0.0.5/24"})
    assert r.status_code == 403
    with client.session_transaction() as s:  # тот же клиент — возвращаем админа
        s["user"] = "admin"

    # неизвестное действие → 400
    r = client.post("/api/network/address", json={"action": "nope"})
    assert r.status_code == 400 and r.get_json()["ok"] is False

    # вызов core-оператора с нормализованным grace
    seen = {}

    def _fake(iface, cidr, insurance_grace=0):
        seen.update(iface=iface, cidr=cidr, grace=insurance_grace)
        return {"ok": True, "phase": "commit"}

    monkeypatch.setattr(cn, "set_address", _fake)
    r = client.post("/api/network/address",
                    json={"action": "set", "iface": "eth0", "cidr": "10.0.0.5/24",
                          "insurance_grace": 9999})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert seen == {"iface": "eth0", "cidr": "10.0.0.5/24", "grace": 300}

    # ошибка валидации core → 200 с ok=False (контракт UI)
    monkeypatch.setattr(cn, "set_address",
                        lambda i, c, insurance_grace=0: {"ok": False,
                                                         "error": "недопустимый CIDR",
                                                         "phase": None})
    r = client.post("/api/network/address",
                    json={"action": "set", "iface": "eth0", "cidr": "bad"})
    assert r.status_code == 200 and r.get_json()["ok"] is False


def test_route_wifi_scan_delegates_to_core(client, monkeypatch):
    monkeypatch.setattr(cn, "wifi_scan",
                        lambda ifaces=None, timeout=15: {"ok": True, "networks": [], "seen": ifaces})
    d = client.get("/api/wifi/scan").get_json()
    assert d["ok"] is True and d["seen"] == ["wlan1", "wlan0"]


def test_route_nettools_uses_core_process(client, monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = "PING ok"
        stderr = ""

    def _run(cmd, timeout=30, **kw):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setattr(cn.process, "run", _run)
    d = client.post("/api/nettools/ping", json={"host": "ya.ru"}).get_json()
    assert d == {"ok": True, "output": "PING ok"}
    assert seen["cmd"][:2] == ["ping", "-c"]

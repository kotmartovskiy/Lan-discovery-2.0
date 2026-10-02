# -*- coding: utf-8 -*-
"""Unit: capabilities layer (STEP 7 + PHASE 2.0-1) — достоверность + reliability."""
import os
import time

import pytest

import app
import modules.auth as auth
from core import capabilities as caps_mod
from core.capabilities import (
    TOOL_PROBES,
    VALID_RELIABILITY,
    VALID_STATES,
    _cap,
    collect,
    probe_tools,
)

# Группы таксономии 2.0 (PHASE 2.0-1).
GROUPS_V2 = {
    "network": ("ethernet", "wifi", "wifi_ap", "multiple_interfaces"),
    "hardware": ("usb", "gpio", "uart", "rs485", "i2c", "spi", "onewire"),
    "radio": ("sdr", "subghz"),
    "camera": ("usb", "ip"),
    "media": ("audio", "video"),
    "service": ("systemd", "docker"),
}
STORAGE_V2 = ("emmc", "sd", "hdd", "local", "removable", "smart")


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


def _check_cap(c, where):
    assert c["state"] in VALID_STATES, (where, c)
    assert c["reliability"] in VALID_RELIABILITY, (where, c)
    if c["state"] == "unknown":
        assert c["reliability"] == "unverified", (where, c)


def test_collect_shape_and_reliability():
    d = collect()
    for key in ("board", "storage", "thermal", "tools", "checked_at"):
        assert key in d, key
    assert d["board"]["reliability"] in VALID_RELIABILITY
    assert d["board"]["model"] is None or isinstance(d["board"]["model"], str)
    for key in ("emmc", "sd", "hdd"):
        _check_cap(d["storage"][key], "storage." + key)
    _check_cap(d["thermal"], "thermal")
    for name in TOOL_PROBES:
        assert name in d["tools"], name
        _check_cap(d["tools"][name], "tools." + name)


def test_probe_tools_covers_all_probes():
    tools = probe_tools()
    assert set(tools) == set(TOOL_PROBES)


def test_cap_helper():
    assert _cap("absent", "detected") == {
        "state": "absent", "reliability": "detected"}
    c = _cap("present", "measured", value=52.0)
    assert c["value"] == 52.0
    assert "value" not in _cap("unknown", "unverified")


def test_api_capabilities_shape(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    d = r.get_json()
    for key in ("board", "storage", "thermal", "tools", "checked_at"):
        assert key in d, key
    _check_cap(d["storage"]["emmc"], "api.storage.emmc")
    _check_cap(d["thermal"], "api.thermal")


def test_capabilities_page_renders(client):
    r = client.get("/capabilities")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Возможности системы" in html
    assert "/api/capabilities" in html
    # semantic-состояние хотя бы одно (present/absent/unknown покрытие полное)
    assert ("status-ok" in html) or ('class="na"' in html) \
        or ("status-unknown" in html)


def test_app_probes_single_source():
    """app.CAPABILITY_PROBES — алиас core.capabilities.TOOL_PROBES."""
    assert app.CAPABILITY_PROBES is TOOL_PROBES


# --- PHASE 2.0-1: таксономия Capabilities 2.0 -----------------------------

def test_collect_v2_taxonomy():
    """collect() содержит все группы 2.0 с валидными состояниями."""
    d = collect()
    for g, keys in GROUPS_V2.items():
        assert g in d, g
        assert set(d[g]) == set(keys), (g, sorted(d[g]))
        for k in keys:
            _check_cap(d[g][k], g + "." + k)
    assert set(d["storage"]) == set(STORAGE_V2)
    for k in STORAGE_V2:
        _check_cap(d["storage"][k], "storage." + k)
    for k in ("hostapd", "docker"):
        assert k in d["tools"], k
        _check_cap(d["tools"][k], "tools." + k)


def test_network_wifi_ap_logic(monkeypatch):
    """AP требует и Wi-Fi-интерфейс, и ПО; без AP-софта — absent/detected."""
    monkeypatch.setattr(caps_mod, "_net_ifaces", lambda: (["eth0"], ["wlan0"]))
    monkeypatch.setattr(caps_mod.shutil, "which", lambda n: None)
    g = caps_mod._network()
    assert g["ethernet"]["state"] == "present"
    assert g["wifi"]["state"] == "present"
    assert g["wifi_ap"]["state"] == "absent"
    assert g["wifi_ap"]["reliability"] == "detected"
    assert g["multiple_interfaces"]["state"] == "present"
    assert g["multiple_interfaces"]["value"] == 2


def test_network_wifi_ap_with_hostapd(monkeypatch):
    monkeypatch.setattr(caps_mod, "_net_ifaces", lambda: ([], ["wlan0"]))
    monkeypatch.setattr(caps_mod.shutil, "which", lambda n: "/usr/sbin/hostapd")
    g = caps_mod._network()
    assert g["wifi_ap"] == {
        "state": "present", "reliability": "detected", "value": "hostapd"}
    assert g["multiple_interfaces"]["state"] == "absent"
    assert g["multiple_interfaces"]["value"] == 1


def test_wifi_ap_needs_wifi(monkeypatch):
    """Без Wi-Fi-интерфейса AP невозможен — absent без поиска ПО."""
    monkeypatch.setattr(caps_mod, "_net_ifaces", lambda: (["eth0"], []))
    g = caps_mod._network()
    assert g["wifi_ap"] == {"state": "absent", "reliability": "detected"}


def test_network_source_down(monkeypatch):
    """sysfs недоступен → unknown/unverified, не absent."""
    monkeypatch.setattr(caps_mod, "_net_ifaces", lambda: None)
    g = caps_mod._network()
    for k in GROUPS_V2["network"]:
        _check_cap(g[k], "network." + k)
        assert g[k]["state"] == "unknown"
        assert g[k]["reliability"] == "unverified"


def test_sdr_detected(monkeypatch):
    monkeypatch.setattr(
        caps_mod, "_usb_pairs",
        lambda: [("0bda", "2832"), ("1234", "5678")])
    assert caps_mod._sdr() == {
        "state": "present", "reliability": "detected", "value": ["rtl-sdr"]}


def test_sdr_absent_after_search(monkeypatch):
    """USB прочитан, известных чипов нет → отсутствие после поиска."""
    monkeypatch.setattr(caps_mod, "_usb_pairs", lambda: [])
    assert caps_mod._sdr() == {"state": "absent", "reliability": "detected"}


def test_sdr_source_down(monkeypatch):
    monkeypatch.setattr(caps_mod, "_usb_pairs", lambda: None)
    c = caps_mod._sdr()
    assert c == {"state": "unknown", "reliability": "unverified"}


def test_subghz_flipper(monkeypatch):
    monkeypatch.setattr(caps_mod, "_usb_pairs", lambda: [("0483", "5740")])
    c = caps_mod._subghz()
    assert c["state"] == "present"
    assert c["value"] == "flipper zero"


def test_subghz_spi_blocks_absence(monkeypatch):
    """Есть SPI → CC1101 не исключён → unknown, не absent."""
    monkeypatch.setattr(caps_mod, "_usb_pairs", lambda: [])
    monkeypatch.setattr(
        caps_mod, "_scan",
        lambda patterns: _cap("present", "detected", value=["spi0"]))
    assert caps_mod._subghz() == {"state": "unknown", "reliability": "unverified"}


def test_subghz_absent(monkeypatch):
    monkeypatch.setattr(caps_mod, "_usb_pairs", lambda: [])
    monkeypatch.setattr(
        caps_mod, "_scan", lambda patterns: _cap("absent", "detected"))
    assert caps_mod._subghz() == {"state": "absent", "reliability": "detected"}


def test_docker_socket_present(monkeypatch, tmp_path):
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    monkeypatch.setattr(caps_mod, "_DOCKER_SOCKETS", (str(sock),))
    c = caps_mod._docker()
    assert c == {
        "state": "present", "reliability": "detected", "value": "docker.sock"}


def test_docker_cli_only_is_unknown(monkeypatch, tmp_path):
    """CLI без сокета: демон не подтверждён → unknown, не absent."""
    monkeypatch.setattr(caps_mod, "_DOCKER_SOCKETS",
                        (str(tmp_path / "no.sock"),))
    monkeypatch.setattr(caps_mod.shutil, "which", lambda n: "/usr/bin/docker")
    c = caps_mod._docker()
    assert c["state"] == "unknown"
    assert c["reliability"] == "unverified"
    assert "docker CLI" in c["value"]


def test_docker_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(caps_mod, "_DOCKER_SOCKETS",
                        (str(tmp_path / "no.sock"),))
    monkeypatch.setattr(caps_mod.shutil, "which", lambda n: None)
    assert caps_mod._docker() == {"state": "absent", "reliability": "detected"}


def test_camera_ip_no_config(monkeypatch, tmp_path):
    """Нет конфига → сеть не сканируем → unknown, не absent."""
    monkeypatch.setattr(caps_mod, "_CAMERAS_CONFIG",
                        str(tmp_path / "none.json"))
    assert caps_mod._camera_ip() == {"state": "unknown",
                                     "reliability": "unverified"}


def test_camera_ip_empty_config(monkeypatch, tmp_path):
    p = tmp_path / "cameras.json"
    p.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(caps_mod, "_CAMERAS_CONFIG", str(p))
    assert caps_mod._camera_ip() == {"state": "absent",
                                     "reliability": "detected"}


def test_camera_ip_configured(monkeypatch, tmp_path):
    p = tmp_path / "cameras.json"
    p.write_text('["rtsp://cam1"]', encoding="utf-8")
    monkeypatch.setattr(caps_mod, "_CAMERAS_CONFIG", str(p))
    assert caps_mod._camera_ip() == {"state": "present",
                                     "reliability": "measured", "value": 1}


def test_storage_local_rules():
    mk = lambda s: {"state": s, "reliability": "detected"}
    caps = {"emmc": mk("absent"), "sd": mk("absent"), "hdd": mk("absent")}
    assert caps_mod._storage_local(caps) == {
        "state": "absent", "reliability": "detected"}
    caps["hdd"] = mk("present")
    assert caps_mod._storage_local(caps)["state"] == "present"
    caps["hdd"] = mk("unknown")
    assert caps_mod._storage_local(caps) == {
        "state": "unknown", "reliability": "unverified"}


def test_storage_smart_detected(monkeypatch):
    monkeypatch.setattr(caps_mod.shutil, "which",
                        lambda n: "/usr/sbin/smartctl")
    assert caps_mod._storage_smart() == {
        "state": "present", "reliability": "detected", "value": "smartctl"}


def test_glob_empty_when_root_exists(tmp_path):
    """Корень существует, совпадений нет → пусто (неудачный поиск)."""
    assert caps_mod._glob(str(tmp_path / "no-such-dir" / "*.txt")) == []


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="только Linux (/proc)")
def test_audio_cards_measured(monkeypatch, tmp_path):
    f = tmp_path / "cards"
    f.write_text(" 0 [PCH ]: HDA Intel PCH\n", encoding="utf-8")
    monkeypatch.setattr(caps_mod, "_ALSA_CARDS", str(f))
    assert caps_mod._audio() == {"state": "present",
                                 "reliability": "measured", "value": ["PCH"]}


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="только Linux (/proc)")
def test_audio_no_cards_after_search(monkeypatch, tmp_path):
    f = tmp_path / "cards"
    f.write_text("no soundcards found...\n", encoding="utf-8")
    monkeypatch.setattr(caps_mod, "_ALSA_CARDS", str(f))
    assert caps_mod._audio() == {"state": "absent",
                                 "reliability": "detected"}


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="только Linux (/proc)")
def test_audio_missing_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(caps_mod, "_ALSA_CARDS", str(tmp_path / "none"))
    assert caps_mod._audio() == {"state": "absent",
                                 "reliability": "detected"}


def test_api_capabilities_v2_shape(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    d = r.get_json()
    for g in GROUPS_V2:
        assert g in d, g
        for k in GROUPS_V2[g]:
            _check_cap(d[g][k], "api." + g + "." + k)
    for k in STORAGE_V2:
        _check_cap(d["storage"][k], "api.storage." + k)

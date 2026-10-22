# -*- coding: utf-8 -*-
"""Unit: спасательный AP (modules/rescue)."""
import pytest

from modules import rescue
from core import process


@pytest.fixture()
def rcfg(tmp_path, monkeypatch):
    monkeypatch.setattr(rescue, "CONF_PATH", str(tmp_path / "rescue.json"))
    monkeypatch.setattr(rescue, "CONF_DIR", str(tmp_path / "rescue"))
    monkeypatch.setattr(rescue, "HOSTAPD_CONF",
                        str(tmp_path / "rescue" / "hostapd.conf"))
    monkeypatch.setattr(rescue, "DNSMASQ_CONF",
                        str(tmp_path / "rescue" / "dnsmasq.conf"))
    monkeypatch.setattr(rescue, "PID_HOSTAPD",
                        str(tmp_path / "rescue" / "hostapd.pid"))
    monkeypatch.setattr(rescue, "PID_DNSMASQ",
                        str(tmp_path / "rescue" / "dnsmasq.pid"))
    return tmp_path


def test_defaults_when_no_config(rcfg):
    cfg = rescue.load_config()
    assert cfg["ssid"] == "LAN-Rescue"
    assert cfg["channel"] == 6
    assert cfg["subnet"] == "192.168.4.1/24"


def test_save_config_ok(rcfg):
    cfg = rescue.save_config(ssid="MyNet", passphrase="secret123",
                             channel=1, subnet="10.9.9.1/24")
    assert cfg["ssid"] == "MyNet" and cfg["passphrase"] == "secret123"
    again = rescue.load_config()
    assert again["ssid"] == "MyNet" and again["channel"] == 1


def test_save_config_validation(rcfg):
    with pytest.raises(ValueError):
        rescue.save_config(ssid="x" * 33)
    with pytest.raises(ValueError):
        rescue.save_config(passphrase="short")
    with pytest.raises(ValueError):
        rescue.save_config(channel=0)
    with pytest.raises(ValueError):
        rescue.save_config(channel=20)
    with pytest.raises(ValueError):
        rescue.save_config(subnet="192.168.4.1")
    with pytest.raises(ValueError):
        rescue.save_config(iface="eth0;reboot")


def test_uplink_parsing(monkeypatch):
    monkeypatch.setattr(process, "out",
                        lambda cmd, timeout=5: "default via 1.2.3.4 dev wlan0\n")
    assert rescue.uplink_iface() == "wlan0"
    monkeypatch.setattr(process, "out", lambda cmd, timeout=5: "")
    assert rescue.uplink_iface() == ""


def test_ifaces_marks_uplink(monkeypatch):
    monkeypatch.setattr(rescue, "_default_ifaces", lambda: ["wlan0", "wlan1"])
    monkeypatch.setattr(rescue, "uplink_iface", lambda: "wlan0")
    monkeypatch.setattr(process, "out",
                        lambda cmd, timeout=5: "inet 10.0.0.5/24" if "wlan1" in cmd else "")
    out = rescue.ifaces()
    assert out[0]["name"] == "wlan0" and out[0]["uplink"] is True
    assert out[1]["uplink"] is False and out[1]["has_ip"] is True
    assert out[1]["ip"] == "10.0.0.5/24"


def test_start_refuses_uplink(rcfg, monkeypatch):
    rescue.save_config(iface="wlan0")
    monkeypatch.setattr(rescue, "uplink_iface", lambda: "wlan0")
    monkeypatch.setattr(rescue, "tools",
                        lambda: {"hostapd": True, "dnsmasq": True})
    with pytest.raises(RuntimeError, match="default route"):
        rescue.start()


def test_start_requires_tools(rcfg, monkeypatch):
    rescue.save_config(iface="wlan1")
    monkeypatch.setattr(rescue, "uplink_iface", lambda: "wlan0")
    monkeypatch.setattr(rescue, "tools",
                        lambda: {"hostapd": False, "dnsmasq": False})
    with pytest.raises(RuntimeError, match="hostapd"):
        rescue.start()


def test_start_no_free_iface(rcfg, monkeypatch):
    monkeypatch.setattr(rescue, "_default_ifaces", lambda: ["wlan0"])
    monkeypatch.setattr(rescue, "uplink_iface", lambda: "wlan0")
    monkeypatch.setattr(rescue, "tools",
                        lambda: {"hostapd": True, "dnsmasq": True})
    with pytest.raises(RuntimeError, match="нет свободного"):
        rescue.start()


def test_write_confs_open_and_secured(rcfg):
    cfg = rescue.load_config()
    cfg["iface"] = "wlan1"
    cfg["passphrase"] = ""
    rescue._write_confs(cfg)
    text = open(rescue.HOSTAPD_CONF).read()
    assert "ssid=LAN-Rescue" in text and "wpa=2" not in text
    cfg["passphrase"] = "secret123"
    rescue._write_confs(cfg)
    text = open(rescue.HOSTAPD_CONF).read()
    assert "wpa=2" in text and "wpa_passphrase=secret123" in text
    dnsm = open(rescue.DNSMASQ_CONF).read()
    assert "dhcp-range=192.168.4.10,192.168.4.100,12h" in dnsm


def test_running_pid_logic(rcfg, monkeypatch):
    import os
    assert rescue.running() == {"hostapd": False, "dnsmasq": False}
    os.makedirs(rescue.CONF_DIR, exist_ok=True)
    with open(rescue.PID_HOSTAPD, "w") as f:
        f.write("1234")
    monkeypatch.setattr(rescue, "_alive", lambda pid: pid == 1234)
    assert rescue.running()["hostapd"] is True
    assert rescue.running()["dnsmasq"] is False


def test_stop_clears_pidfiles(rcfg, monkeypatch):
    import os
    import subprocess
    os.makedirs(rescue.CONF_DIR, exist_ok=True)
    with open(rescue.PID_HOSTAPD, "w") as f:
        f.write("999999")
    monkeypatch.setattr(rescue, "_alive", lambda pid: False)
    monkeypatch.setattr(process, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, "", ""))
    rescue.stop()
    assert not os.path.exists(rescue.PID_HOSTAPD)


def test_install_whitelist(rcfg):
    with pytest.raises(RuntimeError, match="whitelist"):
        rescue.install("tcpdump")
    with pytest.raises(RuntimeError, match="whitelist"):
        rescue.install("hostapd; rm -rf /")


def test_install_rejects_when_running(rcfg, monkeypatch):
    from modules import rescue as r
    monkeypatch.setitem(r._install, "running", True)
    with pytest.raises(RuntimeError, match="уже выполняется"):
        r.install("hostapd")


def test_status_shape(rcfg, monkeypatch):
    monkeypatch.setattr(rescue, "tools",
                        lambda: {"hostapd": True, "dnsmasq": False})
    monkeypatch.setattr(rescue, "ifaces", lambda: [])
    monkeypatch.setattr(rescue, "uplink_iface", lambda: "")
    st = rescue.status()
    assert st["tools"]["hostapd"] is True
    assert st["running"] is False and st["hostapd"] is False
    assert "ssid" in st["config"] and "passphrase" not in st["config"]

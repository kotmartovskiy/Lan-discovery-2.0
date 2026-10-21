# -*- coding: utf-8 -*-
"""Unit: модуль дом/сигнализация (modules/home)."""
import pytest

from modules import home


@pytest.fixture()
def home_db(devices_db, monkeypatch):
    monkeypatch.setattr(home, "_init_done", False)
    home.init_home_db()
    yield devices_db


def test_valid_chip_line_ok():
    assert home._valid_chip_line("gpiochip0", 18) == ("gpiochip0", 18)
    assert home._valid_chip_line("/dev/gpiochip1", "5") == ("gpiochip1", 5)


def test_valid_chip_line_bad():
    with pytest.raises(ValueError):
        home._valid_chip_line("evil;rm -rf /", 1)
    with pytest.raises(ValueError):
        home._valid_chip_line("gpiochip0/../ttyS0", 1)
    with pytest.raises(ValueError):
        home._valid_chip_line("gpiochip0", -1)
    with pytest.raises(ValueError):
        home._valid_chip_line("gpiochip0", 99999)
    with pytest.raises(ValueError):
        home._valid_chip_line("", 1)


def test_zone_add_list_delete(home_db):
    zid = home.add_zone("дверь", "gpiochip0", 18, 0)
    zones = home.list_zones()
    assert len(zones) == 1
    z = zones[0]
    assert z["name"] == "дверь" and z["chip"] == "gpiochip0" and z["line"] == 18
    assert z["trigger_level"] == 0 and z["enabled"] is True
    assert home.delete_zone(zid) is True
    assert home.list_zones() == []


def test_zone_add_validation(home_db):
    with pytest.raises(ValueError):
        home.add_zone("", "gpiochip0", 18)
    with pytest.raises(ValueError):
        home.add_zone("x", "bad", 18)


def test_alarm_arm_disarm(home_db):
    st = home.alarm_state()
    assert st["armed"] is False and st["triggered"] is False
    st = home.arm()
    assert st["armed"] is True
    st = home.disarm()
    assert st["armed"] is False and st["triggered"] is False


def test_siren_zone_set_clear(home_db):
    home.set_siren_zone("gpiochip0", 19)
    con_z = home.list_zones()
    siren = [z for z in con_z if z["name"] == "__siren__"]
    assert len(siren) == 1 and siren[0]["line"] == 19
    home.set_siren_zone(None, 0)
    siren = [z for z in home.list_zones() if z["name"] == "__siren__"]
    assert siren == []
    with pytest.raises(ValueError):
        home.set_siren_zone("bad", 1)


def test_siren_manual_requires_config(home_db):
    with pytest.raises(RuntimeError):
        home.set_siren(True)


def test_mqtt_save_validation(home_db):
    home.mqtt_save("192.168.3.10", 1883, "lanpanel", True)
    cfg = home.mqtt_config()
    assert cfg["host"] == "192.168.3.10" and cfg["enabled"] is True
    with pytest.raises(ValueError):
        home.mqtt_save("h", 99999, "p", True)


def test_mqtt_prefix_sanitized(home_db):
    home.mqtt_save("h", 1883, "my/topic; rm -rf /", True)
    assert home.mqtt_config()["prefix"] == "my/topicrm-rf"


def test_mqtt_send_disabled_noop(home_db):
    # enabled=0 → best-effort False без вызова mosquitto_pub
    assert home._mqtt_send("state", "{}") is False


def test_status_shape(home_db):
    st = home.status()
    for key in ("armed", "triggered", "gpio", "chips", "temp", "zones",
                "mqtt", "mqtt_tool", "siren_configured", "loop"):
        assert key in st


def test_emit_uses_namespace(home_db, monkeypatch):
    captured = []
    monkeypatch.setattr(home.events, "emit",
                        lambda name, **kw: captured.append(name))
    home._emit("home.alarm_triggered", "тревога", zone="дверь")
    assert captured == ["home.alarm_triggered"]

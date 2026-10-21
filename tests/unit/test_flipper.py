# -*- coding: utf-8 -*-
"""Unit: модуль hardware-инструментов (modules/flipper)."""
import io
import os

import pytest

from modules import flipper


def test_safe_upload_name_strips_path():
    assert flipper._safe_upload_name("../../etc/passwd") == "passwd"
    assert flipper._safe_upload_name("/abs/path/firmware.bin") == "firmware.bin"


def test_safe_upload_name_sanitizes():
    name = flipper._safe_upload_name("прошивка v1 (final).bin")
    assert "/" not in name and "\\" not in name
    assert name.endswith(".bin")


def test_safe_upload_name_empty():
    assert flipper._safe_upload_name("") == "firmware.bin"
    assert flipper._safe_upload_name(None) == "firmware.bin"


def test_valid_upload_rejects_outside(tmp_path):
    assert flipper._valid_upload("/etc/passwd") is False
    assert flipper._valid_upload(flipper.UPLOAD_DIR + "/../passwd") is False
    assert flipper._valid_upload("") is False
    assert flipper._valid_upload(None) is False


def test_valid_upload_accepts_inside(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\x00\x01")
    assert flipper._valid_upload(str(p)) is True


def test_save_upload_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    data = b"\xde\xad\xbe\xef" * 1000
    path, size = flipper.save_upload("fw.bin", io.BytesIO(data))
    assert size == len(data)
    assert os.path.isfile(path)
    with open(path, "rb") as f:
        assert f.read() == data


def test_save_upload_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(flipper, "MAX_UPLOAD", 1024)
    with pytest.raises(RuntimeError):
        flipper.save_upload("big.bin", io.BytesIO(b"x" * 2048))


def test_install_rejects_non_whitelist():
    with pytest.raises(RuntimeError):
        flipper.install("nmap; rm -rf /")
    with pytest.raises(RuntimeError):
        flipper.install("tcpdump")


def test_prog_avrdude_validates_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    fw = tmp_path / "fw.hex"
    fw.write_text(":00000001FF")
    # programmer/mcu не из списков
    with pytest.raises(RuntimeError):
        flipper.prog_avrdude(str(fw), "evil;rm", "m328p")
    with pytest.raises(RuntimeError):
        flipper.prog_avrdude(str(fw), "arduino", "../../mcu")
    # port не tty
    with pytest.raises(RuntimeError):
        flipper.prog_avrdude(str(fw), "arduino", "m328p", port="/dev/sda")
    # файл вне UPLOAD_DIR
    with pytest.raises(RuntimeError):
        flipper.prog_avrdude("/etc/passwd", "arduino", "m328p")


def test_prog_esptool_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00")
    with pytest.raises(RuntimeError):
        flipper.prog_esptool(str(fw), "/dev/ttyUSB0", chip="evil")
    with pytest.raises(RuntimeError):
        flipper.prog_esptool(str(fw), "/dev/ttyUSB0", action="exec")
    with pytest.raises(RuntimeError):
        flipper.prog_esptool(str(fw), "/dev/sda")
    with pytest.raises(RuntimeError):
        flipper.prog_esptool(str(fw), "/dev/ttyUSB0", address="rm -rf /")


def test_prog_stm32flash_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(flipper, "UPLOAD_DIR", str(tmp_path))
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00")
    with pytest.raises(RuntimeError):
        flipper.prog_stm32flash(str(fw), "/dev/ttyS2")  # не USB/ACM
    with pytest.raises(RuntimeError):
        flipper.prog_stm32flash(str(fw), "/dev/ttyUSB0", action="evil")


def test_detect_shape():
    d = flipper.detect()
    assert "tools" in d and "devices" in d and "serial" in d
    for key in ("rtl_sdr", "rtl_433", "avrdude", "esptool", "stm32flash",
                "pyserial"):
        assert key in d["tools"]


def test_pager_status_idle():
    st = flipper.pager_status()
    assert st["running"] is False


def test_serial_status_idle():
    st = flipper.serial_status()
    assert st["open"] is False

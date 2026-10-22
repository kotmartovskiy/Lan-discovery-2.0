# -*- coding: utf-8 -*-
"""Unit: core.disktool — листинг, охранники, parted, образы, API."""
import io as _io
import time

import pytest

import app
import modules.auth as auth
from core import disktool


@pytest.fixture()
def client(monkeypatch):
    app.app.config["TESTING"] = True
    old_csrf = app.app.config["WTF_CSRF_ENABLED"]
    app.app.config["WTF_CSRF_ENABLED"] = False
    import modules.disktool_routes as dtr
    dtr._rate.clear()
    monkeypatch.setattr(
        auth, "load_users",
        lambda: {"admin": {"enabled": True, "role": "admin"}},
    )
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "admin"
        s["login_ts"] = time.time()
    yield c
    app.app.config["WTF_CSRF_ENABLED"] = old_csrf


LSBLK = {
    "blockdevices": [
        {"name": "mmcblk2", "type": "disk", "size": 15000000000,
         "tran": "mmc", "rm": False, "model": "eMMC",
         "children": [
             {"name": "mmcblk2p2", "type": "part", "size": 14000000000,
              "fstype": "ext4", "label": "rootfs", "mountpoint": "/"},
         ]},
        {"name": "sda", "type": "disk", "size": 250000000000,
         "tran": "usb", "rm": True, "model": "HDD",
         "children": [
             {"name": "sda1", "type": "part", "size": 240000000000,
              "fstype": "ext4", "label": "data", "mountpoint": "/srv"},
         ]},
        {"name": "sdb", "type": "disk", "size": 8000000000,
         "tran": "usb", "rm": True, "model": "FLASH",
         "children": [
             {"name": "sdb1", "type": "part", "size": 7000000000,
              "fstype": "vfat", "label": "USB", "mountpoint": None},
         ]},
        {"name": "zram0", "type": "disk", "size": 1000000000,
         "tran": None, "rm": False, "model": None},
    ],
}


@pytest.fixture()
def fake_disks(monkeypatch):
    monkeypatch.setattr(disktool, "root_disk_name",
                        lambda mounts_text=None: "mmcblk2")
    disks = disktool.parse_lsblk(LSBLK)
    monkeypatch.setattr(disktool, "lsblk_disks", lambda: disks)
    return disks


def test_parse_lsblk_flags(monkeypatch):
    monkeypatch.setattr(disktool, "root_disk_name",
                        lambda mounts_text=None: "mmcblk2")
    disks = disktool.parse_lsblk(LSBLK)
    by = {d["name"]: d for d in disks}
    assert by["mmcblk2"]["system"] is True
    assert by["mmcblk2"]["operable"] is False
    assert by["sda"]["mounted"] is True
    assert by["sda"]["operable"] is False
    assert by["sdb"]["operable"] is True
    assert by["zram0"]["operable"] is False
    assert by["sdb"]["partitions"][0]["mountpoint"] == ""
    assert by["sdb"]["partitions"][0]["name"] == "sdb1"


def test_root_disk_name_variants():
    assert disktool.root_disk_name("/dev/mmcblk2p2 / ext4 rw 0 0") == "mmcblk2"
    assert disktool.root_disk_name("/dev/sda1 / ext4 rw 0 0") == "sda"
    assert disktool.root_disk_name("/dev/nvme0n1p3 / ext4 rw 0 0") == "nvme0n1"
    assert disktool.root_disk_name("") is None


def test_dev_path_validation():
    assert disktool.dev_path("sda") == "/dev/sda"
    with pytest.raises(ValueError):
        disktool.dev_path("../etc/passwd")
    with pytest.raises(ValueError):
        disktool.dev_path("sda1;rm -rf")


def test_check_whole_disk_guards(fake_disks):
    disks = fake_disks
    assert disktool.check_whole_disk(disks, "sdb") == (True, "")
    ok, reason = disktool.check_whole_disk(disks, "mmcblk2")
    assert ok is False and "системный" in reason
    ok, reason = disktool.check_whole_disk(disks, "sda")
    assert ok is False and "монтирован" in reason
    ok, reason = disktool.check_whole_disk(disks, "zram0")
    assert ok is False
    ok, reason = disktool.check_whole_disk(disks, "nope")
    assert ok is False and "не обнаружен" in reason
    ok, reason = disktool.check_whole_disk(disks, "sda/../")
    assert ok is False


def test_check_partition(fake_disks):
    disks = fake_disks
    ok, _ = disktool.check_partition(disks, "sdb1")
    assert ok is True
    ok, reason = disktool.check_partition(disks, "sda1")
    assert ok is False and "смонтирован" in reason
    ok, reason = disktool.check_partition(disks, "mmcblk2p2")
    assert ok is False
    ok, reason = disktool.check_partition(disks, "sdb")
    assert ok is False and "раздел" in reason
    ok, _ = disktool.check_partition(disks, "zzz")
    assert ok is False


def test_parse_parted():
    text = ("BYT;\n"
            "/dev/sdb:8002636288B:8002636288B:512B::gpt:;\n"
            "1:1048576B:400000000B:398951424B:ext4::;\n"
            "2:400000001B:8002636287B:4002636286B:fat32::;\n")
    d = disktool.parse_parted(text)
    assert d["table"] == "gpt"
    assert len(d["partitions"]) == 2
    assert d["partitions"][0]["num"] == 1
    assert d["partitions"][0]["fs"] == "ext4"
    assert d["partitions"][0]["size"] == 398951424
    assert d["partitions"][1]["fs"] == "fat32"


def test_format_partition_validation():
    r = disktool.format_partition("sdb1", "zfs", "")
    assert r["ok"] is False
    r = disktool.format_partition("sdb1", "vfat", "X" * 20)
    assert r["ok"] is False and "метка" in r["error"]


def test_format_partition_mkfs_command(monkeypatch):
    seen = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, timeout=30):
        seen.append(cmd)
        return R()

    monkeypatch.setattr(disktool.process, "run", fake_run)
    r = disktool.format_partition("sdb1", "ext4", "DATA")
    assert r["ok"] is True
    assert seen[0] == ["mkfs.ext4", "-F", "-L", "DATA", "/dev/sdb1"]


def test_resolve_image_validation():
    assert disktool.resolve_image("custom:../evil") is None
    assert disktool.resolve_image("custom:ok-name.img") is None
    assert disktool.resolve_image("fd13-floppy-144-boot") is None
    assert disktool.resolve_image("nope") is None


def test_api_disks_ok(client, fake_disks):
    r = client.get("/api/disktool/disks")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] and len(data["disks"]) == 4


def test_api_format_guard_blocks_system(client, fake_disks):
    r = client.post("/api/disktool/format",
                    json={"dev": "mmcblk2p2", "fs": "ext4"})
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert "системн" in err or "смонтирован" in err


def test_api_format_guard_blocks_mounted(client, fake_disks):
    r = client.post("/api/disktool/format",
                    json={"dev": "sda1", "fs": "ext4"})
    assert r.status_code == 400
    assert "смонтирован" in r.get_json()["error"]


def test_api_format_calls_mkfs(client, fake_disks, monkeypatch):
    monkeypatch.setattr(
        disktool, "format_partition",
        lambda part, fs, label="": {"ok": True})
    r = client.post("/api/disktool/format",
                    json={"dev": "sdb1", "fs": "ext4", "label": "X"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_api_wipe_blocked_on_system(client, fake_disks):
    r = client.post("/api/disktool/wipe", json={"dev": "mmcblk2"})
    assert r.status_code == 400


def test_api_requires_login_for_status():
    c = app.app.test_client()
    r = c.get("/api/disktool/status")
    assert r.status_code in (302, 401)


def test_api_images_lists_freedos(client, monkeypatch, tmp_path):
    monkeypatch.setattr(disktool, "IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(disktool, "IMAGE_CUSTOM_DIR", str(tmp_path / "custom"))
    r = client.get("/api/disktool/images")
    data = r.get_json()
    assert data["ok"]
    keys = [i["key"] for i in data["images"]]
    assert "fd13-floppy-144-boot" in keys
    assert "fd13-liteusb" in keys


def test_save_upload_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(disktool, "IMAGE_CUSTOM_DIR", str(tmp_path))
    r = disktool.save_upload("../../evil.img", _io.BytesIO(b"123"))
    assert r["ok"] is False
    r = disktool.save_upload("good.img", _io.BytesIO(b""))
    assert r["ok"] is False
    r = disktool.save_upload("bad.exe", _io.BytesIO(b"abc"))
    assert r["ok"] is False
    r = disktool.save_upload("good.img", _io.BytesIO(b"abc"))
    assert r["ok"] is True and r["size"] == 3


def test_clone_requires_two_disks(client, fake_disks):
    import modules.disktool_routes as dtr
    r = client.post("/api/disktool/clone", json={"src": "sdb", "dst": "sdb"})
    assert r.status_code == 400
    dtr._rate.clear()
    r = client.post("/api/disktool/clone", json={"src": "sdb", "dst": "sda"})
    assert r.status_code == 400  # sda смонтирован

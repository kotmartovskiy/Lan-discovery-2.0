# -*- coding: utf-8 -*-
"""Unit: Storage Core — корни/path, backup targets, клон (PHASE 2.0-7).

Спека §7: единая модель хранения; перенос do_clone (Инвентаризация);
консистентность путей с restore_server/deploy/recovery (legacy вне
корней — значения не меняем).
"""
import os
import re
import subprocess

import pytest

import app
import modules.media_routes as mr
import modules.system_routes as sr
from core import storage

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- корни и path() --------------------------------------------------------

def test_roots_match_spec_tree():
    assert storage.ROOTS == {
        "media": "/srv/media",
        "data": "/srv/data",
        "backup": "/srv/backup",
    }


def test_path_joins_posix():
    assert storage.path("media") == "/srv/media"
    assert storage.path("media", "IPTV") == "/srv/media/IPTV"
    assert storage.path("media", "playlists") == "/srv/media/playlists"
    assert storage.path("backup", "db") == "/srv/backup/db"
    assert storage.path("data", "downloads", "x") == "/srv/data/downloads/x"


def test_path_unknown_root_raises():
    with pytest.raises(ValueError):
        storage.path("tmp")
    with pytest.raises(ValueError):
        storage.path("/etc")


# --- миграция констант модулей ---------------------------------------------

def test_module_constants_single_source():
    """IPTV/MEDIA/PLAYLISTS больше не дублируются строками — path()."""
    assert app.IPTV_DIR == storage.path("media", "IPTV")
    assert mr.IPTV_DIR == storage.path("media", "IPTV")
    assert sr.IPTV_DIR == storage.path("media", "IPTV")
    assert mr.MEDIA_DIR == storage.path("media")
    assert mr.PLAYLISTS_DIR == storage.path("media", "playlists")
    # значения не изменились (1.1-совместимость)
    assert app.IPTV_DIR == "/srv/media/IPTV"
    assert mr.MEDIA_DIR == "/srv/media"


def test_backup_targets_constants():
    assert sr.DB_BACKUP_DIR is storage.DB_BACKUP_DIR
    assert storage.DB_BACKUP_DIR == "/srv/backup-db"
    assert storage.EMMC_BACKUP_PATH == "/srv/backup-system/emmc.img.zst"


def _repo_file(name):
    with open(os.path.join(REPO, name), "r", encoding="utf-8") as f:
        return f.read()


def test_db_backup_path_consistent_across_standalone_tools():
    """restore_server/deploy/recovery читают тот же путь, что панель."""
    m = re.search(r'DB_BACKUP_DIR = "([^"]+)"', _repo_file("restore_server.py"))
    assert m and m.group(1) == storage.DB_BACKUP_DIR
    assert 'd = "%s"' % storage.DB_BACKUP_DIR in _repo_file("deploy/backup-db.sh")
    assert storage.DB_BACKUP_DIR in _repo_file("recovery.sh")


def test_emmc_path_migrated_in_system_routes():
    """Хардкоды emmc-пути убраны из system_routes (источник — storage)."""
    src = _repo_file("modules/system_routes.py")
    assert "/srv/backup-system/emmc.img.zst" not in src
    assert "core_storage.EMMC_BACKUP_PATH" in src
    # standalone restore_server остаётся на своей строке (не импортирует core)
    assert storage.EMMC_BACKUP_PATH in _repo_file("restore_server.py")


def test_clone_migrated_to_core_storage():
    src = _repo_file("modules/system_routes.py")
    assert "core_storage.clone_disk(" in src
    assert "_dd_progress_watcher" not in src


# --- клонирование ----------------------------------------------------------

def _fake_proc(rc=0, stderr="", raises=None):
    class P:
        pid = 4242
        returncode = rc
        killed = False

        def __init__(self):
            self._raised = False

        def poll(self):
            return rc

        def communicate(self, timeout=None):
            if raises and not self._raised:
                self._raised = True
                raise raises
            return ("", stderr)

        def kill(self):
            self.killed = True

    return P()


def test_clone_percent_scale():
    assert storage._clone_percent(0, 1000) == 5
    assert storage._clone_percent(500, 1000) == 50
    assert storage._clone_percent(1000, 1000) == 95
    assert storage._clone_percent(9999, 1000) == 95
    assert storage._clone_percent(100, 0) == 5


def test_clone_validates_devices(monkeypatch):
    monkeypatch.setattr(storage.os.path, "exists", lambda p: False)
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda")
    assert r["ok"] is False and "исходное" in r["error"]
    monkeypatch.setattr(storage.os.path, "exists",
                        lambda p: p == "/dev/mmcblk2")
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda")
    assert r["ok"] is False and "целевое" in r["error"]


def test_clone_happy_path(monkeypatch):
    proc = _fake_proc(rc=0)
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)
    monkeypatch.setattr(storage.process, "run", lambda *a, **k: None)
    monkeypatch.setattr(storage, "_block_size_bytes", lambda d: 1000)
    monkeypatch.setattr(storage.subprocess, "Popen", lambda *a, **k: proc)
    events = []
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda",
                           on_progress=lambda p, t: events.append((p, t)))
    assert r == {"ok": True, "error": None}
    assert events and events[0] == (5, "Копирование eMMC...")  # sync-фаза


def test_clone_error_from_stderr(monkeypatch):
    proc = _fake_proc(rc=1, stderr="dd: invalid argument")
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)
    monkeypatch.setattr(storage.process, "run", lambda *a, **k: None)
    monkeypatch.setattr(storage, "_block_size_bytes", lambda d: 1000)
    monkeypatch.setattr(storage.subprocess, "Popen", lambda *a, **k: proc)
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda")
    assert r["ok"] is False and r["error"] == "dd: invalid argument"


def test_clone_timeout_kills_process(monkeypatch):
    proc = _fake_proc(raises=subprocess.TimeoutExpired(cmd="dd", timeout=1))
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)
    monkeypatch.setattr(storage.process, "run", lambda *a, **k: None)
    monkeypatch.setattr(storage, "_block_size_bytes", lambda d: 1000)
    monkeypatch.setattr(storage.subprocess, "Popen", lambda *a, **k: proc)
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda", timeout=1)
    assert r["ok"] is False and "таймаут" in r["error"]
    assert proc.killed is True


def test_clone_unexpected_exception(monkeypatch):
    def _boom(*a, **k):
        raise OSError("fork failed")
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)
    monkeypatch.setattr(storage.process, "run", _boom)
    r = storage.clone_disk("/dev/mmcblk2", "/dev/sda")
    assert r["ok"] is False and "fork failed" in r["error"]


def test_clone_watcher_stops_on_cancel(monkeypatch):
    """should_continue False → прогресс не шлётся (до sleep, без задержки)."""
    proc = _fake_proc(rc=None)

    def _poll_none():
        return None
    proc.poll = _poll_none
    events = []
    storage._clone_watcher(proc, 1000, lambda p, t: events.append(p),
                           should_continue=lambda: False)
    assert events == []


def test_clone_watcher_skips_without_total(monkeypatch):
    events = []
    storage._clone_watcher(_fake_proc(rc=None), 0,
                           lambda p, t: events.append(p), None)
    assert events == []

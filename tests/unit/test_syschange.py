# -*- coding: utf-8 -*-
"""Unit: system change transactions (PHASE 2.0-16, §26)."""
import io
import json
import os

import pytest

from core import syschange


class FakeApt:
    """Инжектируемый run_cmd: apt/dpkg с моделью состояния."""

    def __init__(self, pre_installed=(), simulate_ok=True,
                 install_ok=True, take_effect=True):
        self.installed = set(pre_installed)
        self.simulate_ok = simulate_ok
        self.install_ok = install_ok
        self.take_effect = take_effect
        self.calls = []

    def __call__(self, cmd, timeout=None):
        self.calls.append(list(cmd))
        if cmd[0] == "apt-get":
            if cmd[1] == "--version":
                return True, "apt-get 2.6.1"
            if cmd[1] == "install" and "-s" in cmd:
                if self.simulate_ok:
                    return True, "Inst pkg-a [1.0] (1.1 debian [amd64])\n"
                return False, "E: Unable to locate package pkg-a"
            if cmd[1] == "install":
                if not self.install_ok:
                    return False, "E: dep problems"
                if self.take_effect:
                    self.installed |= set(cmd[3:])
                return True, ""
            if cmd[1] == "remove":
                self.installed -= set(cmd[3:])
                return True, ""
            if cmd[1] == "dselect-upgrade":
                return True, ""
        if cmd[0] == "dpkg":
            if "--get-selections" in cmd:
                return True, "".join(
                    p + " install\n" for p in sorted(self.installed))
            return True, ""
        if cmd[0] == "dpkg-query":
            names = cmd[4:]
            return True, "".join(
                "%s install ok installed\n" % n
                for n in names if n in self.installed)
        return True, ""


def _apt_op(pkgs=("pkg-a",)):
    return [{"type": "apt_install", "packages": list(pkgs)}]


# --- конвейер apt ------------------------------------------------------------

def test_apt_pipeline_success(tmp_path):
    run_cmd = FakeApt(pre_installed=["pkg-b"])
    log = []
    res = syschange.run(_apt_op(), run_cmd,
                        backup_root=str(tmp_path / "bak"), log=log.append)

    assert res["ok"] is True
    assert res["state"] == "committed"
    argvs = [" ".join(c) for c in run_cmd.calls]

    def i(sub):
        return next(idx for idx, a in enumerate(argvs) if sub in a)

    # preflight → backup (query-состояния + selections) → apply → verify
    assert i("apt-get --version") < i("install -s -y") < i("dpkg-query")
    assert i("dpkg-query") < i("dpkg --get-selections")
    assert i("dpkg --get-selections") < i("apt-get install -y pkg-a")
    # два опроса dpkg-query: до и после apply
    assert sum(1 for a in argvs if a.startswith("dpkg-query")) == 2

    assert "pkg-a" in run_cmd.installed
    assert os.path.isfile(os.path.join(res["dir"], "manifest.json"))
    assert os.path.isfile(os.path.join(res["dir"], "selections.txt"))
    for mark in ("preflight", "backup", "apply", "verify"):
        assert any(mark in s for s in res["steps"]), mark
    assert any("preflight" in s for s in log)


def test_preflight_fail_no_backup(tmp_path):
    run_cmd = FakeApt(simulate_ok=False)
    bak = str(tmp_path / "bak")
    with pytest.raises(syschange.SystemChangeError) as ei:
        syschange.run(_apt_op(), run_cmd, backup_root=bak)
    assert ei.value.txn["state"] == "preflight-failed"
    # бэкап не создавался, apply не запускался
    assert not os.path.exists(bak) or not os.listdir(bak)
    assert not any(c[:3] == ["apt-get", "install", "-y"] for c in run_cmd.calls)


def test_apply_fail_rollback(tmp_path):
    run_cmd = FakeApt(pre_installed=["pkg-b"], install_ok=False)
    with pytest.raises(syschange.SystemChangeError) as ei:
        syschange.run(_apt_op(), run_cmd, backup_root=str(tmp_path / "bak"))
    assert ei.value.txn["state"] == "rolled_back"
    steps = ei.value.txn["steps"]
    assert any("rollback" in s for s in steps)
    # selections восстановлены (sh -c dpkg --set-selections + dselect-upgrade)
    assert any(c[0] == "sh" for c in run_cmd.calls)
    assert any(c[:2] == ["apt-get", "dselect-upgrade"] for c in run_cmd.calls)
    # манифест остался для диагностики
    assert os.path.isfile(os.path.join(ei.value.txn["dir"], "manifest.json"))


def test_verify_fail_removes_new_pkg(tmp_path):
    run_cmd = FakeApt()

    def bad_verify(txn):
        return False, "диск недоступен"

    with pytest.raises(syschange.SystemChangeError) as ei:
        syschange.run(_apt_op(), run_cmd,
                      backup_root=str(tmp_path / "bak"), verify=bad_verify)
    assert "диск недоступен" in str(ei.value)
    assert ei.value.txn["state"] == "rolled_back"
    # пакет был установлен apply → rollback снимает его
    assert any(c[:3] == ["apt-get", "remove", "-y"] for c in run_cmd.calls)
    assert "pkg-a" not in run_cmd.installed


def test_unknown_op_rejected(tmp_path):
    run_cmd = FakeApt()
    with pytest.raises(syschange.SystemChangeError) as ei:
        syschange.run([{"type": "evil"}], run_cmd,
                      backup_root=str(tmp_path / "bak"))
    assert ei.value.txn["state"] == "preflight-failed"
    assert run_cmd.calls == []  # даже apt-get --version не вызывался


# --- file_write ---------------------------------------------------------------

def test_file_write_backup_and_manual_rollback(tmp_path):
    path = str(tmp_path / "cfg.txt")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("old\n")
    run_cmd = FakeApt()
    res = syschange.run(
        [{"type": "file_write", "path": path, "content": "new\n"}],
        run_cmd, backup_root=str(tmp_path / "bak"))
    assert res["ok"] and res["state"] == "committed"
    with io.open(path, encoding="utf-8") as f:
        assert f.read() == "new\n"

    rb = syschange.rollback(res["dir"], run_cmd)
    assert rb["ok"] is True
    with io.open(path, encoding="utf-8") as f:
        assert f.read() == "old\n"


def test_file_created_removed_on_rollback(tmp_path):
    path = str(tmp_path / "fresh.txt")
    run_cmd = FakeApt()
    res = syschange.run(
        [{"type": "file_write", "path": path, "content": "hi\n"}],
        run_cmd, backup_root=str(tmp_path / "bak"))
    assert os.path.isfile(path)
    rb = syschange.rollback(res["dir"], run_cmd)
    assert rb["ok"] is True
    assert not os.path.exists(path)


# --- потребитель: module_manager._install_manifest ---------------------------

def test_consumer_install_manifest_apt_ok(tmp_path, monkeypatch):
    import modules.module_manager as mm

    run_cmd = FakeApt()
    monkeypatch.setattr(mm, "_run", run_cmd)
    monkeypatch.setattr(syschange, "BACKUP_ROOT", str(tmp_path / "bak"))
    res = mm._install_manifest({"id": "m", "deps": {"apt": ["pkg-a"]}})
    assert res["ok"] is True
    assert any("apt install pkg-a: OK" in s for s in res["log"])
    assert any("preflight" in s for s in res["log"])
    assert "pkg-a" in run_cmd.installed


def test_consumer_install_manifest_apt_fail(tmp_path, monkeypatch):
    import modules.module_manager as mm

    run_cmd = FakeApt(install_ok=False)
    monkeypatch.setattr(mm, "_run", run_cmd)
    monkeypatch.setattr(syschange, "BACKUP_ROOT", str(tmp_path / "bak"))
    res = mm._install_manifest({"id": "m", "deps": {"apt": ["pkg-a"]}})
    assert res["ok"] is False
    assert any("apt install pkg-a: FAIL" in s for s in res["log"])
    assert any("rollback" in s for s in res["log"])
    assert "pkg-a" not in run_cmd.installed

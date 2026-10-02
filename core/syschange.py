# -*- coding: utf-8 -*-
"""System change transactions (спека §26).

Системные изменения (apt-пакеты, запись файлов) проходят конвейер:

    preflight → backup → apply → verify → rollback

Application rollback (code/config/db/modules) — в update.sh; здесь
только system-level (§26: не утверждать, что application rollback
откатывает ОС). Бэкапы: BACKUP_ROOT/<id>/ (manifest.json, pre.json,
selections.txt, files/).

run_cmd — инжектируемый исполнитель (тесты/стенд):
callable(argv, timeout=...) -> (ok, out).
"""
import io
import json
import os
import shutil
import time
import uuid

BACKUP_ROOT = "/var/lib/lan-discovery/rollback"

_TYPES = ("apt_install", "file_write")


class SystemChangeError(RuntimeError):
    """Ошибка транзакции; .txn — dict состояния для диагностики."""

    def __init__(self, message, txn=None):
        super().__init__(message)
        self.txn = txn or {}


def _log(log, text):
    if log:
        try:
            log(text)
        except Exception:
            pass


def _dpkg_installed(names, run_cmd):
    """{name: installed} по dpkg-query (стиль core.module_loader).

    Пустой вывод при ненулевом rc — ошибка (сistема без dpkg/битый
    запрос); отсутствующие в выводе пакеты = не установлены.
    """
    names = list(dict.fromkeys(names))
    if not names:
        return {}
    ok, out = run_cmd(
        ["dpkg-query", "-W", "-f", "${Package} ${Status}\n"] + names,
        timeout=60,
    )
    states = {}
    for line in (out or "").splitlines():
        parts = line.rsplit(" ", 3)
        if len(parts) == 4:
            states[parts[0]] = parts[1] == "install" and parts[2] == "ok"
    if not states and not ok:
        raise SystemChangeError(
            "dpkg-query: не смог получить состояния пакетов: %s"
            % ((out or "")[-500:],))
    for n in names:
        states.setdefault(n, False)
    return states


def _preflight(ops, run_cmd):
    if not ops:
        raise SystemChangeError("preflight: пустой список изменений")
    for op in ops:
        t = op.get("type")
        if t not in _TYPES:
            raise SystemChangeError(
                "preflight: неизвестный тип изменения %r" % (t,))
        if t == "apt_install":
            pkgs = op.get("packages") or []
            if not isinstance(pkgs, list) or not pkgs:
                raise SystemChangeError("preflight: apt_install без пакетов")
            ok, out = run_cmd(["apt-get", "--version"], timeout=30)
            if not ok:
                raise SystemChangeError(
                    "preflight: apt-get недоступен: %s" % ((out or "")[-300:],))
            ok, out = run_cmd(
                ["apt-get", "install", "-s", "-y"] + pkgs, timeout=300)
            if not ok:
                raise SystemChangeError(
                    "preflight: симуляция apt не прошла: %s"
                    % ((out or "")[-500:],))
        else:  # file_write
            path = op.get("path")
            content = op.get("content")
            if not path or not os.path.isabs(path):
                raise SystemChangeError(
                    "preflight: file_write требует абсолютного пути (получено %r)"
                    % (path,))
            if not isinstance(content, str):
                raise SystemChangeError("preflight: content должен быть строкой")


def _backup(ops, d, run_cmd, log):
    """Снимок состояния в d; возвращает manifest."""
    manifest = {"created": time.strftime("%d.%m.%Y %H:%M:%S"),
                "files": [], "apt": [], "selections": None}
    os.makedirs(d, mode=0o700, exist_ok=True)
    files_dir = os.path.join(d, "files")

    for i, op in enumerate(ops):
        if op["type"] == "file_write":
            path = op["path"]
            rec = {"path": path, "existed": os.path.isfile(path),
                   "backup": None}
            if rec["existed"]:
                os.makedirs(files_dir, exist_ok=True)
                rec["backup"] = "%03d_%s" % (i, os.path.basename(path))
                shutil.copy2(path, os.path.join(files_dir, rec["backup"]))
            manifest["files"].append(rec)
        else:
            pkgs = list(op["packages"])
            pre = _dpkg_installed(pkgs, run_cmd)
            manifest["apt"].append({"packages": pkgs, "pre": pre})

    ok, out = run_cmd(["dpkg", "--get-selections"], timeout=60)
    if ok and out and out.strip():
        with io.open(os.path.join(d, "selections.txt"), "w",
                     encoding="utf-8") as f:
            f.write(out)
        manifest["selections"] = "selections.txt"

    with io.open(os.path.join(d, "manifest.json"), "w",
                 encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    _log(log, "backup: %s" % d)
    return manifest


def _apply(ops, run_cmd, log):
    for op in ops:
        if op["type"] == "apt_install":
            pkgs = op["packages"]
            ok, out = run_cmd(["apt-get", "install", "-y"] + pkgs,
                              timeout=900)
            _log(log, "apply: apt install %s: %s"
                 % (" ".join(pkgs), "OK" if ok else "FAIL"))
            if not ok:
                raise SystemChangeError(
                    "apply: apt install failed: %s" % ((out or "")[-500:],))
        else:
            path = op["path"]
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = path + ".syschange.tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(op["content"])
            os.replace(tmp, path)
            _log(log, "apply: запись файла %s: OK" % path)


def _verify(ops, run_cmd, verify, txn, log):
    for op in ops:
        if op["type"] == "apt_install":
            states = _dpkg_installed(op["packages"], run_cmd)
            missing = sorted(n for n, st in states.items() if not st)
            if missing:
                raise SystemChangeError(
                    "verify: пакеты не установлены: %s" % ", ".join(missing))
            _log(log, "verify: пакеты установлены: %s"
                 % " ".join(op["packages"]))
        else:
            path = op["path"]
            try:
                with io.open(path, encoding="utf-8") as f:
                    got = f.read()
            except Exception as e:
                raise SystemChangeError(
                    "verify: файл %s не читается: %s" % (path, e))
            if got != op["content"]:
                raise SystemChangeError(
                    "verify: содержимое %s не совпало" % path)
            _log(log, "verify: файл %s: OK" % path)
    if verify is not None:
        r = verify(txn)
        ok, msg = (r if isinstance(r, tuple) else (bool(r), ""))
        if not ok:
            raise SystemChangeError("verify: %s" % (msg or "кастомная проверка"))
        _log(log, "verify: кастомная проверка: OK")


def rollback(txn_dir, run_cmd, log=None):
    """Восстановление по manifest.json из txn_dir (идемпотентно).

    Возвращает {"ok": bool, "errors": [...], "steps": [...]}.
    """
    errors = []
    steps = []

    def _step(text):
        steps.append(text)
        _log(log, text)

    try:
        with io.open(os.path.join(txn_dir, "manifest.json"),
                     encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        return {"ok": False,
                "errors": ["manifest не читается: %s" % e],
                "steps": steps}

    # --- файлы: вернуть/удалить
    for rec in manifest.get("files") or []:
        path = rec["path"]
        try:
            if rec.get("existed") and rec.get("backup"):
                shutil.copy2(os.path.join(txn_dir, "files", rec["backup"]),
                             path)
                _step("rollback: файл восстановлен: %s" % path)
            elif os.path.isfile(path):
                os.remove(path)
                _step("rollback: созданный файл удалён: %s" % path)
        except Exception as e:
            errors.append("файл %s: %s" % (path, e))

    # --- apt: снять новые пакеты, вернуть selections (best effort)
    apt_recs = manifest.get("apt") or []
    if apt_recs:
        to_remove = []
        for rec in apt_recs:
            names = rec["packages"]
            try:
                now = _dpkg_installed(names, run_cmd)
            except SystemChangeError as e:
                errors.append(str(e))
                continue
            for n in names:
                if not rec["pre"].get(n) and now.get(n):
                    to_remove.append(n)
        if to_remove:
            ok, out = run_cmd(["apt-get", "remove", "-y"] + sorted(set(to_remove)),
                              timeout=900)
            if ok:
                _step("rollback: сняты пакеты: %s" % " ".join(sorted(set(to_remove))))
            else:
                errors.append("apt remove: %s" % ((out or "")[-300:],))
        if manifest.get("selections"):
            sel = os.path.join(txn_dir, manifest["selections"])
            ok, out = run_cmd(
                ["sh", "-c", "dpkg --set-selections < %s"
                 % _sh_quote(sel)], timeout=120)
            if ok:
                ok2, out2 = run_cmd(["apt-get", "dselect-upgrade", "-y"],
                                    timeout=900)
                if ok2:
                    _step("rollback: selections восстановлены")
                else:
                    errors.append("dselect-upgrade: %s" % ((out2 or "")[-300:],))
            else:
                errors.append("dpkg --set-selections: %s" % ((out or "")[-300:],))

    return {"ok": not errors, "errors": errors, "steps": steps}


def _sh_quote(s):
    return "'%s'" % s.replace("'", "'\\''")


def run(ops, run_cmd, backup_root=BACKUP_ROOT, verify=None, log=None):
    """preflight → backup → apply → verify (+rollback при ошибке).

    Возвращает {"ok": True, "id", "dir", "steps", "state": "committed"};
    при ошибке — SystemChangeError с .txn (state: preflight-failed /
    backup-failed / rolled_back / rollback-failed).
    """
    txn = {"id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
           "dir": None, "steps": [], "state": "new"}

    def _step(text):
        txn["steps"].append(text)
        _log(log, text)

    # --- 1. preflight
    try:
        _preflight(ops, run_cmd)
    except SystemChangeError as e:
        txn["state"] = "preflight-failed"
        e.txn = txn
        raise
    _step("preflight: OK")

    # --- 2. backup
    d = os.path.join(backup_root, txn["id"])
    try:
        manifest = _backup(ops, d, run_cmd, _step)
    except SystemChangeError as e:
        txn["dir"] = d
        txn["state"] = "backup-failed"
        e.txn = txn
        raise
    except Exception as e:
        txn["dir"] = d
        txn["state"] = "backup-failed"
        err = SystemChangeError("backup: %s" % e, txn)
        raise err
    txn["dir"] = d

    # --- 3. apply + 4. verify (+ rollback при ошибке)
    failure = None
    try:
        _apply(ops, run_cmd, _step)
        _verify(ops, run_cmd, verify, txn, _step)
    except SystemChangeError as e:
        failure = e
    except Exception as e:
        failure = SystemChangeError("apply: %s" % e)

    if failure is not None:
        rb = rollback(d, run_cmd, log=_step)
        txn["state"] = "rolled_back" if rb["ok"] else "rollback-failed"
        failure.txn = txn
        if not rb["ok"]:
            failure.args = (str(failure) + " | rollback: "
                            + "; ".join(rb["errors"]),)
        raise failure

    txn["state"] = "committed"
    _step("транзакция зафиксирована: %s" % txn["id"])
    return {"ok": True, "id": txn["id"], "dir": d,
            "steps": txn["steps"], "state": txn["state"]}

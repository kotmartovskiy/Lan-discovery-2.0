#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_check — дрейф-контроль «репозиторий ↔ сервер» (PHASE 16, задача 61).

Сверяет md5 git-трекаемых файлов локального репо с файлами на сервере
панели. Серверный обход (код встроен, запускается по SSH) даёт три
поля на файл: сырой md5, md5 после нормализации CRLF→LF и относительный
путь — так «только перевод строк» определяется без скачивания файлов.

Запуск:
  python tools/sync_check.py                     # SSH к LAN_SSH_HOST (по умолчанию X96)
  python tools/sync_check.py --host 192.168.3.243 --user root
  python tools/sync_check.py --filelist fl.txt    # без SSH: готовый filelist
  python tools/sync_check.py --include-docs       # сверять и docs/

Секреты — только из окружения:
  LAN_SSH_PASS      пароль SSH (Windows/plink; иначе — ключ или агент)
  LAN_SSH_HOSTKEY   hostkey plink (по умолчанию — X96)

Exit codes: 0 — синхронно (EOL-only и «только в git» неблокирующие),
1 — расхождения (content diff / только на сервере / недозалитый код),
2 — ошибка запуска (нет SSH, хост недоступен, битый filelist).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

DEFAULT_HOST = os.environ.get("LAN_SSH_HOST", "192.168.3.243")
DEFAULT_USER = os.environ.get("LAN_SSH_USER", "root")
DEFAULT_HOSTKEY = os.environ.get(
    "LAN_SSH_HOSTKEY", "SHA256:TpeCxMP+uAf7CSX8QKr097yGDjf+BQEOhhgGmRDn8po")
DEFAULT_PREFIX = os.environ.get("LAN_PANEL_PREFIX", "/opt/lan-discovery")

# В git, но по регламенту не деплоится на сервер (и не сверяется):
# документация/репозиторный инвентарь + deploy-скрипты (запускаются из
# репо/локально, панелью на сервере не исполняются).
SKIP_LOCAL = ("docs/", "weather-monitor/", ".github/",
              ".gitattributes", ".gitignore", "AGENTS.md", "ROADMAP.md",
              "UI_UX_AUDIT.md", "README.md", "LICENSE",
              "deploy/", "deploy.py", "deploy_restore_server.sh",
              "deploy_templates.py", "remote_edit.py",
              "restore-server.service", "restore_server.py",
              "tools/demo_lint.py",
              "tools/sync_check_daily.cmd", "tools/setup_sync_task.ps1")

# Серверный генератор filelist: 'md5raw  md5norm  relpath'.
# BASE подставляется первым аргументом (sys.argv[1]).
HOST_SCRIPT = r'''
import hashlib, os, sys
BASE = sys.argv[1] if len(sys.argv) > 1 else "/opt/lan-discovery"
EXCLUDE_SUB = ("__pycache__", ".git")

def dig(b):
    return hashlib.md5(b).hexdigest()

def norm(b):
    return b.replace(b"\r\n", b"\n")

out = []
def add(p, rel):
    try:
        with open(p, "rb") as f:
            b = f.read()
        out.append(dig(b) + "  " + dig(norm(b)) + "  " + rel)
    except Exception as e:
        out.append("ERR-" + str(e) + "  -  " + rel)

for rel in ["app.py", "requirements.txt", "requirements-dev.txt",
            "pytest.ini", "install.sh", "update.sh", "recovery.sh",
            "network_check.py", "CHANGELOG.md"]:
    p = os.path.join(BASE, rel)
    if os.path.isfile(p):
        add(p, rel)

for tree in ["modules", "core", "roles", "templates", "static", "games",
             "tools", "deploy", "docs", "tests"]:
    root = os.path.join(BASE, tree)
    if not os.path.isdir(root):
        continue
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE_SUB]
        for f in sorted(fn):
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, BASE).replace(os.sep, "/")
            base = rel.rsplit("/", 1)[-1]
            if ".backup" in base or base.endswith("~") or \
               base.endswith((".db", ".pyc")):
                continue
            add(p, rel)
print("\n".join(sorted(out)))
'''


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def norm_eol(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def skipped(rel: str, include_docs: bool = False) -> bool:
    """True — файл по регламенту не деплоится и не сверяется."""
    for s in SKIP_LOCAL:
        if s == "docs/" and include_docs:
            continue            # --include-docs снимает только этот пункт
        if rel == s or rel.startswith(s):
            return True
    return False


def parse_filelist(text: str):
    """'md5raw [md5norm]  relpath' → ({rel: (raw, norm|None)}, [ошибки]).

    Строки 'ERR-...' (файл не прочитан на сервере) попадают и в отчёт
    ошибок, и в filelist — compare покажет их как content diff.
    """
    res = {}
    errors = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p for p in line.split("  ") if p != ""]
        if len(parts) == 3:
            raw, nrm, rel = parts
        elif len(parts) == 2 and parts[0].startswith("ERR"):
            errors.append(line)
            continue
        elif len(parts) == 2:
            # старый формат (2 колонки): нормализация неизвестна
            raw, rel = parts
            nrm = None
        else:
            errors.append(line)
            continue
        if rel == "-":
            continue
        if raw.startswith("ERR"):
            errors.append(line)
            nrm = None
        res[rel] = (raw, nrm)
    return res, errors


def local_snapshot(repo_root: pathlib.Path, include_docs: bool = False):
    """git ls-files → {rel: bytes}; пары EOL нет — байты как в рабочем дереве."""
    out = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=str(repo_root))
    files = {}
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        if not include_docs and skipped(rel):
            continue
        p = repo_root / rel
        if p.is_file():
            files[rel] = p.read_bytes()
    return files


def compare(filelist: dict, local: dict, include_docs: bool = False):
    """Ядро сверки. → report: exact/eol/content/only_local/only_remote (списки)."""
    remote = {rel: v for rel, v in filelist.items()
              if include_docs or not skipped(rel)}
    exact, eol, content = [], [], []
    for rel, b in sorted(local.items()):
        if rel not in remote:
            continue
        raw_srv, norm_srv = remote[rel]
        raw_loc = md5_bytes(b)
        if raw_loc == raw_srv:
            exact.append(rel)
            continue
        loc_norm = md5_bytes(norm_eol(b))
        if norm_srv is not None and loc_norm == norm_srv:
            eol.append(rel)
        else:
            content.append(rel)
    only_local = sorted(r for r in local if r not in remote)
    only_remote = sorted(r for r in remote if r not in local)
    return {"exact": exact, "eol": eol, "content": content,
            "only_local": only_local, "only_remote": only_remote}


def fetch_filelist(host, user, prefix, hostkey=DEFAULT_HOSTKEY):
    """SSH/plink: выполнить HOST_SCRIPT на сервере, вернуть текст filelist."""
    pw = os.environ.get("LAN_SSH_PASS")
    if os.name == "nt":
        plink = shutil.which("plink") or r"C:\Program Files\PuTTY\plink.exe"
        if not shutil.which("plink") and not os.path.isfile(plink):
            raise RuntimeError("plink не найден (PuTTY в PATH?)")
        cmd = [plink, "-batch", "-ssh", "-hostkey", hostkey]
        if pw:
            cmd += ["-pw", pw]
        cmd += ["%s@%s" % (user, host), "python3 - " + prefix]
    else:
        if pw:
            raise RuntimeError(
                "на POSIX используйте SSH-ключ (LAN_SSH_PASS не поддерживается)")
        ssh = shutil.which("ssh") or "ssh"
        cmd = [ssh, "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=accept-new",
               "%s@%s" % (user, host), "python3 - " + prefix]
    proc = subprocess.run(cmd, input=HOST_SCRIPT.encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("SSH-команда упала (%d): %s"
                           % (proc.returncode,
                              proc.stderr.decode(errors="replace").strip()))
    return proc.stdout.decode("utf-8", "replace")


def find_repo_root():
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL)
    return pathlib.Path(out.decode().strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Дрейф-контроль repo ↔ сервер")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--prefix", default=DEFAULT_PREFIX,
                    help="каталог панели на сервере")
    ap.add_argument("--filelist", help="готовый filelist вместо SSH")
    ap.add_argument("--include-docs", action="store_true")
    ap.add_argument("--repo", help="путь к репо (по умолчанию — текущий git)")
    args = ap.parse_args(argv)

    try:
        repo = pathlib.Path(args.repo) if args.repo else find_repo_root()
    except Exception as e:
        print("sync_check: не найден git-репо: %s" % e, file=sys.stderr)
        return 2

    if args.filelist:
        try:
            text = pathlib.Path(args.filelist).read_text(encoding="utf-8")
        except OSError as e:
            print("sync_check: %s" % e, file=sys.stderr)
            return 2
    else:
        try:
            text = fetch_filelist(args.host, args.user, args.prefix)
        except Exception as e:
            print("sync_check: SSH-сбор не удался: %s" % e, file=sys.stderr)
            return 2

    filelist, errs = parse_filelist(text)
    if errs:
        print("sync_check: строк с ошибками на сервере: %d" % len(errs))
        for e in errs[:5]:
            print("   ", e)

    local = local_snapshot(repo, include_docs=args.include_docs)
    rep = compare(filelist, local, include_docs=args.include_docs)

    print("sync_check: %s ↔ %s:%s" % (repo, args.host, args.prefix))
    print("exact=%d  eol_only=%d  content_diff=%d  only_local=%d  "
          "only_remote=%d"
          % (len(rep["exact"]), len(rep["eol"]), len(rep["content"]),
             len(rep["only_local"]), len(rep["only_remote"])))
    for title, key in (("CONTENT DIFF (внимание)", "content"),
                       ("только на сервере (нет в git)", "only_remote"),
                       ("только в git (не залито на сервер)", "only_local"),
                       ("EOL-only (не блокирует)", "eol")):
        if rep[key]:
            print("--- %s ---" % title)
            for r in rep[key]:
                print("   ", r)

    return 1 if (rep["content"] or rep["only_local"] or rep["only_remote"]) else 0


if __name__ == "__main__":
    sys.exit(main())

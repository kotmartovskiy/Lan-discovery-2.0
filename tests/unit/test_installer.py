# -*- coding: utf-8 -*-
"""Installer 2.0 (Спецификация §25) — цепочка, hw-detect, dry-run."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(  # tests/unit -> tests -> корень репо
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read_install_sh():
    path = os.path.join(ROOT, "install.sh")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_install_sh_covers_spec_25_chain():
    """§25: preflight → hardware detection → dependencies → core → modules
    → configuration → systemd → health — ровно в этом порядке в main()."""
    text = _read_install_sh()
    # блок main()
    start = text.index("main() {")
    end = text.index("\n}", start)
    body = text[start:end]
    chain = []
    for word in body.split():
        if word.startswith("step_"):
            chain.append(word[len("step_"):].rstrip(";"))
    assert chain == [
        "preflight", "hw", "code", "deps", "venv", "modules",
        "config", "db", "unit", "health",
    ], chain
    # §25: health на python urllib — вызовов curl нет (minimal без curl)
    assert "curl -" not in text
    # §25: отчёт hardware detection
    assert "hw-detect.json" in text
    # minimal: опциональные пакеты не роняют установку
    assert "best-effort" in text
    # не предполагать плату: нет плато-специфики
    assert "odroid" not in text.lower() and "raspberry" not in text.lower()


def _bash_candidate():
    """Рабочий bash: WSL-shim на Windows не считается рабочим."""
    cands = [shutil.which("bash")] if shutil.which("bash") else []
    cands += [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for cand in cands:
        if not cand or not os.path.exists(cand):
            continue
        try:
            r = subprocess.run([cand, "-c", "echo ok"],
                               capture_output=True, timeout=30)
        except OSError:
            continue
        if r.returncode == 0:
            return cand
    return None


def test_install_sh_syntax_and_dry_run():
    """bash -n + --dry-run --skip-apt: Installer 2.0 проходит по цепочке §25."""
    bash = _bash_candidate()
    if bash is None:
        pytest.skip("bash недоступен")
    r = subprocess.run([bash, "-n", os.path.join(ROOT, "install.sh")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [bash, os.path.join(ROOT, "install.sh"), "--dry-run", "--skip-apt"],
        capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    assert r.returncode == 0, r.stderr
    for step in ("preflight", "hw", "code", "deps", "venv", "modules",
                 "config", "db", "unit", "health"):
        assert "step: %s" % step in r.stdout, "нет шага %s" % step
    assert "DRY-RUN" in r.stdout
    # A-01: config-шаг всегда логирует ветку users.json (сид admin)
    assert "users.json" in r.stdout
    # A-01: config-шаг всегда логирует ветку users.json (сид admin)
    assert "users.json" in r.stdout
    # A-01: config-шаг всегда логирует ветку users.json (сид admin)
    assert "users.json" in r.stdout


def test_hw_detect_report():
    """tools/hw_detect.py — отчёт с platform/дистрибутивом, без плато-специфики."""
    script = os.path.join(ROOT, "tools", "hw_detect.py")
    # stdout-формат
    r = subprocess.run([sys.executable, script], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert set(data["platform"]) == {
        "board", "arch", "system", "emmc", "sd", "hdd", "thermal_zone",
    }
    assert data["platform"]["arch"]
    assert data["python"]
    # --out: запись файла
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "hw-detect.json")
        r = subprocess.run([sys.executable, script, "--out", out],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        with open(out, encoding="utf-8") as f:
            rep = json.load(f)
        assert rep["platform"]["arch"]
        assert "detected_at" in rep and "distro" in rep


def test_install_sh_seeds_first_admin():
    """A-01: step_config сидит users.json — чистая установка получает вход.

    Без users.json load_users() -> {} и панель вообще без входа, хотя
    install.sh и docs/2.0/INSTALL.md обещают admin/1234.
    """
    text = _read_install_sh()
    assert 'USERS="/etc/lan-discovery/users.json"' in text
    start = text.index("step_config() {")
    end = text.index("step_db() {", start)   # граница следующей функции
    body = text[start:end]
    assert "$USERS" in body              # ветка сидинга users.json
    assert "password_hash" in body       # формат совместим с auth.login
    assert "bcrypt" in body              # bcrypt (+ fallback legacy sha256)
    assert "os.replace" in body          # атомарная запись, не обрезанный файл
    assert "enabled" in body
    # идемпотентно: существующий users.json не перезаписывается
    assert "уже есть" in body
    # step_code кладёт network_check.py (C-02)
    assert "network_check.py" in text



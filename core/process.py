# -*- coding: utf-8 -*-
"""Process layer (PHASE 2.0-2): единственная точка запуска внешних команд.

Спека §4: Module → Core API → subsystem → Linux (прямой subprocess из
модулей убирается постепенно, инвентаризация — docs/Инвентаризация-core-2.0.md).

Правила:
- аргументы — список (shell=False по умолчанию); строка только при явном
  shell=True (наследие старых вызовов app._cmd);
- всегда ограничен timeout; вывод — текст utf-8 (errors="replace");
- возвращает subprocess.CompletedProcess — совместимо со старым кодом;
- исключения стандартные (FileNotFoundError, subprocess.TimeoutExpired) —
  потребитель решает, как их маппить (см. core.services.control);
- out() — обёртка «stdout.strip() или ""» (стиль _cmd).
"""
import subprocess


def run(cmd, timeout=30, shell=None, cwd=None, env=None, input=None):
    """Запуск команды, CompletedProcess с захваченным текстовым выводом."""
    if shell is None:
        shell = isinstance(cmd, str)
    return subprocess.run(
        cmd,
        shell=shell,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        input=input,
        encoding="utf-8",
        errors="replace",
    )


def out(cmd, timeout=5, **kwargs):
    """stdout.strip(); любая ошибка запуска (нет бинаря/таймаут) → ""."""
    try:
        return run(cmd, timeout=timeout, **kwargs).stdout.strip()
    except Exception:
        return ""

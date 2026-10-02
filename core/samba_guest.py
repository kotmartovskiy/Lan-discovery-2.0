"""Гостевой доступ Samba: состояние и переключение шар.

Работает с /etc/samba/smb.conf: включение добавляет `guest ok = yes`
в файловые шары (секции с `path`, кроме printers/print$/homes/global),
комментирует `valid users` (префиксом ``# lan-discovery guest: ``,
восстанавливается при выключении) и прописывает в `[global]`
`map to guest = bad user` (если отсутствует). Выключение убирает
truthy `guest ok` и восстанавливает `valid users`.

Каждая запись сопровождается бэкапом `smb.conf.backup-<дата>` и
проверкой `testparm -s` до применения; конфиг перечитывается через
`smbcontrol all reload-config` (fallback: `systemctl reload smbd`).
Состояние всегда читается из самого smb.conf — отдельный флаг не ведётся.
"""
import os
import re
import shutil
import subprocess
import time

from core import process, services

SMB_CONF = "/etc/samba/smb.conf"
MANAGED_PREFIX = "# lan-discovery guest: "
SKIP_SECTIONS = {"global", "printers", "print$", "homes"}

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
VALID_USERS_RE = re.compile(r"^\s*valid users\s*=", re.IGNORECASE)
GUEST_OK_RE = re.compile(r"^(\s*)guest ok\s*=\s*(.+?)\s*$", re.IGNORECASE)
PATH_RE = re.compile(r"^\s*path\s*=", re.IGNORECASE)
MAP_TO_GUEST_RE = re.compile(r"^\s*map to guest\s*=", re.IGNORECASE)
TRUTHY = ("yes", "true", "1")


def _is_truthy(value):
    return value.strip().lower() in TRUTHY


def _is_file_share(name, seg):
    if name.lower() in SKIP_SECTIONS:
        return False
    return any(PATH_RE.match(line) for line in seg)


def _section_ranges(lines):
    """[(name, start, end)] по секциям, end — эксклюзивно."""
    ranges = []
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m:
            if ranges and ranges[-1][2] is None:
                name, start, _ = ranges[-1]
                ranges[-1] = (name, start, i)
            ranges.append((m.group(1).strip(), i, None))
    if ranges and ranges[-1][2] is None:
        name, start, _ = ranges[-1]
        ranges[-1] = (name, start, len(lines))
    return ranges


def _transform_section(seg, enabled, file_share):
    out = []
    has_guest_ok = False
    for idx, line in enumerate(seg):
        if idx == 0:
            out.append(line)
            continue
        if line.startswith(MANAGED_PREFIX):
            out.append(line if enabled else line[len(MANAGED_PREFIX):])
            continue
        if not file_share:
            out.append(line)
            continue
        m = GUEST_OK_RE.match(line)
        if m:
            has_guest_ok = True
            if enabled:
                out.append(m.group(1) + "guest ok = yes")
            elif not _is_truthy(m.group(2)):
                out.append(line)
            continue
        if enabled and VALID_USERS_RE.match(line):
            out.append(MANAGED_PREFIX + line)
            continue
        out.append(line)
    if enabled and file_share and not has_guest_ok:
        out.insert(1, "    guest ok = yes")
    return out


def _ensure_map_to_guest(seg):
    if any(MAP_TO_GUEST_RE.match(line) for line in seg):
        return seg
    return [seg[0], "    map to guest = bad user"] + seg[1:]


def transform(text, enabled):
    """Новый текст smb.conf для желаемого состояния (идемпотентно)."""
    lines = text.splitlines()
    ranges = _section_ranges(lines)
    if not ranges:
        return text
    out = []
    pos = 0
    for name, start, end in ranges:
        out.extend(lines[pos:start])
        pos = end
        seg = lines[start:end]
        if name.lower() == "global":
            seg = _transform_section(seg, enabled, file_share=False)
            if enabled:
                seg = _ensure_map_to_guest(seg)
        else:
            file_share = _is_file_share(name, seg)
            seg = _transform_section(seg, enabled, file_share=file_share)
        out.extend(seg)
    out.extend(lines[pos:])
    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result


def _state(text):
    lines = text.splitlines()
    shares = {}
    for name, start, end in _section_ranges(lines):
        seg = lines[start:end]
        if not _is_file_share(name, seg):
            continue
        guest_ok = False
        restricted = False
        for idx, line in enumerate(seg):
            if idx == 0:
                continue
            if line.startswith(MANAGED_PREFIX):
                continue
            m = GUEST_OK_RE.match(line)
            if m and _is_truthy(m.group(2)):
                guest_ok = True
            if VALID_USERS_RE.match(line):
                restricted = True
        shares[name] = {
            "guest_ok": guest_ok,
            "restricted": restricted,
            "enabled": guest_ok and not restricted,
        }
    enabled = bool(shares) and all(s["enabled"] for s in shares.values())
    return {"enabled": enabled, "shares": shares}


def guest_state(conf_path=SMB_CONF):
    with open(conf_path, "r", encoding="utf-8") as fh:
        return _state(fh.read())


def _reload_smbd():
    try:
        result = process.run(
            ["smbcontrol", "all", "reload-config"], timeout=15
        )
        if result.returncode == 0:
            return None
    except Exception:
        pass
    res = services.control("smbd", "reload", timeout=15)
    if res.get("ok"):
        return None
    return "конфиг записан, но перезагрузить smbd не удалось"


def apply_samba_guest(enabled, conf_path=SMB_CONF):
    """Переключить гостевой доступ. Возвращает dict для API-ответа."""
    with open(conf_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    new_text = transform(text, enabled)
    state = _state(new_text)

    if new_text == text:
        state["ok"] = True
        state["changed"] = False
        return state

    backup = "%s.backup-%s" % (conf_path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(conf_path, backup)

    tmp = conf_path + ".lan-discovery-new"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        try:
            result = process.run(
                ["testparm", "-s", tmp], timeout=20
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "ok": False,
                "error": "testparm недоступен: %s" % exc,
                **_state(text),
            }
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()[:300]
            return {
                "ok": False,
                "error": "testparm отклонил конфиг: %s" % details,
                **_state(text),
            }
        os.replace(tmp, conf_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    error = _reload_smbd()
    state["ok"] = error is None
    state["changed"] = True
    if error:
        state["error"] = error
    return state

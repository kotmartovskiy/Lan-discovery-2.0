# -*- coding: utf-8 -*-
"""Roles layer 2.0 (спека §14): роли = манифесты roles/<id>.json.

Роль описывает СЦЕНАРИЙ устройства, а не просто список включаемого:

    name / description
    required_modules         — без чего роль не работает ("*" — все модули)
    optional_modules         — желательные модули (включаются при наличии)
    capabilities             — возможности среды, core/capabilities (group.key)
    hardware_requirements    — {"arch": [...], "tools": [...], "storage": [...]}
    dependencies             — {"apt": [...], "services": [...]} — системные
    conflicts                — модули, выключаемые этой ролью (кроме ALWAYS_ON)
    recommended_configuration — {ключ: рекомендация} (показ в UI, не применяется)
    security_profile         — бейдж профиля безопасности (не применяется)

Файлы: roles/<id>.json, невалидные не показываются (validate_role).
Состояние активной роли — /etc/lan-discovery/roles.json. Совместимость
МОДУЛЕЙ роли — core.module_loader.compute_status (compat-check, как в 1.1);
ГОТОВНОСТЬ роли (blockers) — архитектура/железо/возможности/зависимости
среды. Применение не ставит модули — только включает/выключает (как было).

API (modules/module_manager.py) — контракты не менялись (аддитивно
расширяется roles_overview()):
    GET  /api/roles, POST /api/roles/<rid>/apply,
    GET  /roles,     POST /roles/<rid>/apply.
"""
import glob
import json
import os
import re
import time

from core import config
from core import manifest as manifest_mod
from core.module_loader import (
    discover_modules,
    module_status,
    set_module_status,
    status_context,
    compute_status,
    _missing_apt_packages,
)

# runtime state (§20): путь — единый справочник core.config
STATE_PATH = config.ROLES_STATE_PATH
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(_CORE_DIR, "roles")

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}$")
_CAP_KEY_RE = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)?$")

# Модули, которые роль НЕ выключает никогда (доступ к настройкам/пользователям,
# управление питанием/сетью, бэкапы БД — безопасность панели).
ALWAYS_ON = (
    "sys-board", "sys-network", "sys-power",
    "sys-settings", "sys-users", "sys-db",
)

_roles_cache = {"ts": 0.0, "data": None}


# --- загрузка и валидация ---------------------------------------------------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _is_str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate_role(m, stem=None):
    """Список ошибок структуры роли-манифеста (пустой [] = валиден).

    Проверяются только присутствующие поля (файл без optional_modules
    валиден). "*" разрешён только в required_modules.
    """
    if not isinstance(m, dict):
        return ["роль не объект JSON"]
    errs = []
    rid = m.get("id")
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        errs.append("id: нужна латиница [a-z0-9_-] (получено %r)" % (rid,))
    elif stem and rid != stem:
        errs.append("id %r не совпадает с именем файла %r.json" % (rid, stem))
    if not isinstance(m.get("name"), str) or not m.get("name"):
        errs.append("name: нужна непустая строка")
    if "description" in m and m["description"] is not None:
        if not isinstance(m["description"], str):
            errs.append("description: ожидается строка")

    req = m.get("required_modules")
    if req is not None and req != "*":
        if not _is_str_list(req):
            errs.append("required_modules: ожидается список строк или \"*\"")
        elif "*" in req and req != ["*"]:
            errs.append("\"*\" можно указывать только одним элементом списка")
    opt = m.get("optional_modules")
    if opt is not None and not _is_str_list(opt):
        errs.append("optional_modules: ожидается список строк")
    if "*" in (opt or []):
        errs.append("\"*\" не поддерживается в optional_modules")

    conf = m.get("conflicts")
    if conf is not None:
        if not _is_str_list(conf):
            errs.append("conflicts: ожидается список строк")
        else:
            overlap = sorted(set(conf) & set(ALWAYS_ON))
            if overlap:
                errs.append("conflicts трогает ALWAYS_ON: %s" % ", ".join(overlap))

    if "capabilities" in m and m["capabilities"] is not None:
        if not _is_str_list(m["capabilities"]):
            errs.append("capabilities: ожидается список строк")
        else:
            for c in m["capabilities"]:
                if not _CAP_KEY_RE.match(c):
                    errs.append("capabilities: ключ %r должен быть group "
                                "или group.key" % c)

    hw = m.get("hardware_requirements")
    if hw is not None:
        if not isinstance(hw, dict):
            errs.append("hardware_requirements: ожидается объект")
        else:
            for k in ("arch", "tools", "storage"):
                if k in hw and hw[k] is not None and not _is_str_list(hw[k]):
                    errs.append("hardware_requirements.%s: ожидается список "
                                "строк" % k)

    deps = m.get("dependencies")
    if deps is not None:
        if not isinstance(deps, dict):
            errs.append("dependencies: ожидается объект {apt, services}")
        else:
            for k in ("apt", "services"):
                if k in deps and deps[k] is not None and not _is_str_list(deps[k]):
                    errs.append("dependencies.%s: ожидается список строк" % k)

    if "recommended_configuration" in m and \
            m["recommended_configuration"] is not None and \
            not isinstance(m["recommended_configuration"], dict):
        errs.append("recommended_configuration: ожидается объект")
    if "security_profile" in m and m["security_profile"] is not None and \
            not isinstance(m["security_profile"], str):
        errs.append("security_profile: ожидается строка")
    return errs


def load_roles(force=False):
    """{id: манифест} из roles/*.json (кэш 5 с; невалидные пропускаются)."""
    now = time.time()
    if not force and _roles_cache["data"] is not None \
            and now - _roles_cache["ts"] < 5:
        return _roles_cache["data"]
    out = {}
    for path in sorted(glob.glob(os.path.join(ROLES_DIR, "*.json"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        m = _read_json(path)
        if m is None or validate_role(m, stem):
            continue  # битая роль не показывается (не ломаем страницу)
        out[m["id"]] = m
    _roles_cache["data"] = out
    _roles_cache["ts"] = now
    return out


def get_role(rid):
    return load_roles().get(rid)


# --- состояние активной роли ------------------------------------------------

def _read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_state(state):
    # Task3: атомарная запись (core.config.write_json_atomic) — сбой/kill
    # в середине записи не портит roles.json; активная роль после
    # перезапуска читается из старого либо нового валидного файла.
    try:
        config.write_json_atomic(STATE_PATH, state)
        return True
    except Exception:
        return False


def active_role():
    """Идентификатор активной роли (дефолт — default)."""
    rid = _read_state().get("active") or "default"
    return rid if rid in load_roles() else "default"


def set_active(rid):
    if rid not in load_roles():
        return False
    state = _read_state()
    state["active"] = rid
    return _write_state(state)


# --- модули роли ------------------------------------------------------------

def role_module_ids(rid):
    """Установленные id модулей роли (required + optional); "*" → все."""
    m = get_role(rid)
    if not m:
        return None
    req = m.get("required_modules")
    opt = m.get("optional_modules") or []
    if req == "*" or (isinstance(req, list) and "*" in req):
        return [x["id"] for x in discover_modules()]
    ids = list(req or []) + list(opt)
    known = {x["id"] for x in discover_modules()}
    return [mid for mid in ids if mid in known]


def _targets(rid):
    """{module_id: enabled_target} для роли (с учётом ALWAYS_ON/conflicts)."""
    m = get_role(rid) or {}
    want = set(role_module_ids(rid) or [])
    conflicts = set(m.get("conflicts") or [])
    out = {}
    for mod in discover_modules():
        mid = mod["id"]
        if mid in ALWAYS_ON:
            out[mid] = True          # ALWAYS_ON сильнее (валидация запрещает overlap)
        elif mid in conflicts:
            out[mid] = False         # конфликт роли выключается
        else:
            out[mid] = mid in want
    return out


# --- blockers: роль против системы -----------------------------------------

def role_blockers(rid, ctx=None):
    """Причины, почему роль не подходит ЭТОЙ системе (список строк).

    Архитектура/железо/возможности (hardware_requirements, capabilities)
    + системные зависимости (dependencies.apt/services). Голые группы
    capabilities не проверяем — не гадаем (как в compute_status).
    Пустой список — роль применима.
    """
    m = get_role(rid)
    if not m:
        return ["роль не найдена"]
    if ctx is None:
        ctx = status_context()
    out = []
    try:
        arch = (m.get("hardware_requirements") or {}).get("arch") or []
        if arch and ctx.get("arch") not in arch:
            out.append("нужна архитектура %s (сейчас %s)"
                       % ("/".join(arch), ctx.get("arch") or "неизвестно"))
        caps = ctx.get("caps") or {}
        for key in m.get("capabilities") or []:
            if "." not in key:
                continue  # голая группа — не гадаем
            grp, item = key.split(".", 1)
            g = caps.get(grp)
            c = g.get(item) if isinstance(g, dict) else None
            if not isinstance(c, dict) or c.get("state") != "present":
                out.append("нет возможности %s" % key)
        hw = m.get("hardware_requirements") or {}
        for t in hw.get("tools") or []:
            c = (caps.get("tools") or {}).get(t)
            if not c or c.get("state") != "present":
                out.append("нет инструмента %s" % t)
        for s in hw.get("storage") or []:
            c = (caps.get("storage") or {}).get(s)
            if not c or c.get("state") != "present":
                out.append("нет накопителя %s" % s)
        deps = m.get("dependencies") or {}
        miss = _missing_apt_packages(deps.get("apt") or [])
        if miss:
            out.append("нет пакетов: %s" % ", ".join(sorted(miss)))
        for svc in deps.get("services") or []:
            from core import services
            st = services.status(svc)
            act = (st or {}).get("active")
            if act in (None, "", "unknown"):
                continue  # не гадаем (нет systemctl и т.п.)
            if act != "active":
                out.append("сервис %s не активен (%s)" % (svc, act))
    except Exception as e:
        out.append("не удалось проверить роль: %s" % e)
    return out


# --- применение -------------------------------------------------------------

def apply_role(rid):
    """Применить роль: вкл/выкл модулей с compat-check.

    Роль с blockers (не подходит по железу/возможностям/зависимостям)
    не применяется — {ok: False, error}. Модули incompatible/
    requires-hardware не включаются (skipped со статусом) — как в 1.1.
    Возвращает {enabled, disabled, skipped, active}.
    """
    if rid not in load_roles():
        return {"ok": False, "error": "unknown role"}
    ctx = status_context()
    blockers = role_blockers(rid, ctx)
    if blockers:
        return {"ok": False,
                "error": "роль не подходит системе: " + "; ".join(blockers)}
    targets = _targets(rid)
    all_pkgs = []
    for m in discover_modules():
        all_pkgs += ((m.get("deps") or {}).get("apt") or [])
    missing = set(_missing_apt_packages(all_pkgs))

    enabled, disabled, skipped = [], [], []
    for m in discover_modules():
        mid = m["id"]
        installed_now, current = module_status(mid)
        if not installed_now:
            continue  # не установлен — включать нечего (не ставим сам state)
        status = compute_status(m, {}, ctx, missing)
        want = targets[mid]
        if want and status in ("incompatible", "requires-hardware"):
            skipped.append({"id": mid, "name": m.get("name", mid),
                            "status": status})
            want = False
        if want and not current:
            set_module_status(mid, enabled=True)
            enabled.append(mid)
        elif not want and current:
            set_module_status(mid, enabled=False)
            disabled.append(mid)
    set_active(rid)
    return {"ok": True, "active": rid, "enabled": enabled,
            "disabled": disabled, "skipped": skipped}


# --- overview ---------------------------------------------------------------

def _raw_ids(m, field):
    """Сырой список id роли (для missing/required/optional в overview)."""
    v = m.get(field)
    if v == "*":
        return ["*"]
    return list(v or [])


def roles_overview():
    """Все роли + compat-check статусы модулей (для /roles и API).

    Аддитивно к 1.1: в каждый профиль входят модули роли + ALWAYS_ON
    (флаг always_on) + конфликтные (role=conflict); плюс контракт 2.0:
    required/optional/missing/not_installed/conflicts/blockers/ready/
    security_profile/recommended_configuration. ready = нет blockers
    (роль применима этой системе); неполнота модулей — отдельные списки.
    """
    all_pkgs = []
    mods_all = discover_modules()
    for m in mods_all:
        all_pkgs += ((m.get("deps") or {}).get("apt") or [])
    missing_pkgs = set(_missing_apt_packages(all_pkgs))
    ctx = status_context()
    active = active_role()
    out = []
    for rid, m in load_roles().items():
        req_ids = _raw_ids(m, "required_modules")
        opt_ids = _raw_ids(m, "optional_modules")
        conf_ids = _raw_ids(m, "conflicts")
        base = set(role_module_ids(rid) or [])
        mods = []
        for mod in mods_all:
            mid = mod["id"]
            always = mid in ALWAYS_ON
            if mid in base:
                kind = "required" if mid in req_ids or req_ids == ["*"] \
                    else "optional"
            elif mid in conf_ids:
                kind = "conflict"
            elif always:
                kind = "always"
            else:
                continue
            mods.append({
                "id": mid,
                "name": mod.get("name", mid),
                "always_on": always,
                "status": compute_status(mod, {}, ctx, missing_pkgs),
                "enabled": module_status(mid)[1],
                "role": kind,
            })
        known = {x["id"] for x in mods_all}
        wanted = [i for i in req_ids + opt_ids if i != "*"]
        missing_ids = [i for i in wanted if i not in known]
        not_installed = [i for i in wanted
                         if i in known and not module_status(i)[0]]
        blockers = role_blockers(rid, ctx)
        out.append({
            "id": rid,
            "name": m.get("name", rid),
            "description": m.get("description", ""),
            "modules": mods,
            "required": req_ids,
            "optional": opt_ids,
            "missing": missing_ids,
            "not_installed": not_installed,
            "conflicts": conf_ids,
            "blockers": blockers,
            "ready": not blockers,
            "security_profile": m.get("security_profile") or "",
            "recommended_configuration":
                m.get("recommended_configuration") or {},
        })
    return {"active": active, "roles": out}

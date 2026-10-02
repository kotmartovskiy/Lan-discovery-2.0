"""Загрузчик модульной системы: манифесты, состояние, nav/help реестры.

Модуль = каталог modules/<id>/ с файлом module.json (манифест):
    id          — уникальный ключ (латиницей)
    name        — название для справки/админки
    description — строка для карточки в /modules
    builtin     — true: код уже в панели (навигация доступна без install)
    url         — главный адрес страницы модуля
    prefixes    — URL-префиксы, которые гасятся when модуль выключен
    tab         — {"title": ..., "order": ...} вкладка в верхнем меню
    page        — ключ активной вкладки (дефолт = id)
    help        — true: рендерить modules/<id>/help.md в конце «Справки»
    deps        — {"apt": [...], "pip": [...], "services": [...], "dirs": [...],
                   "ports": [...]} — ставится кнопкой «Установить зависимости»
    version     — версия модуля (STEP 8; старые манифесты без поля → «Unknown»)
    source      — происхождение (например URL репозитория, "builtin")
    permissions — ключи сетки прав core/manifest.py (network.read, ...) —
                  что требует модуль; показывается как запрос прав в UI
    hardware    — {"arch": [...], "tools": [...], "storage": [...]} —
                  аппаратные требования против core/capabilities (STEP 8)
Поля манифеста 2.0 (спека §10; старые манифесты валидны — нет полей → defaults,
валидация — core/manifest.validate_manifest при установке из каталога):
    capabilities   — ["group"/"group.key"] требования к core/capabilities
    dependencies   — ["<module-id>"] — должны быть установлены и включены
    conflicts      — ["<module-id>"] — несовместимы одновременно
    services       — ["<service>"] — используемые системные сервисы
    configuration  — {} — схема настроек (контракт для UI/SDR)
    role_support   — ["<role-id>"] — роли, включающие модуль (Roles 2.0)
    publisher, min_core_version, max_core_version — trust/совместимость
Состояние (installed/enabled/last_install) хранится в /etc/lan-discovery/modules.json.
Вычисляемые статусы (STEP 8): см. compute_status() — error / incompatible /
requires-hardware / requires-dependency / disabled / active / available /
unknown.
"""
import glob
import json
import os
import time

from core import manifest as manifest_mod
from core import process

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES_DIR = os.path.join(_CORE_DIR, "modules")
STATE_PATH = "/etc/lan-discovery/modules.json"

# Ядро: всегда в навигации, не выключается. group — доменная группа (STEP 3).
CORE_NAV = [
    {"page": "devices", "title": "Устройства", "url": "/", "order": 10, "group": "Устройства"},
    {"page": "history", "title": "История", "url": "/history", "order": 35, "group": "Мониторинг"},
    {"page": "apps", "title": "Приложения", "url": "/apps", "order": 70, "group": "Приложения"},
    {"page": "system", "title": "Система", "url": "/system", "order": 80, "group": "Система"},
    {"page": "capabilities", "title": "Возможности", "url": "/capabilities", "order": 82, "group": "Система"},
    {"page": "roles", "title": "Роли", "url": "/roles", "order": 84, "group": "Система", "admin": True},
    {"page": "modules", "title": "Модули", "url": "/modules", "order": 85, "group": "Система", "admin": True},
    {"page": "about", "title": "О системе", "url": "/about", "order": 90, "group": "Система"},
    {"page": "help", "title": "Справка", "url": "/help", "order": 100, "group": "Помощь"},
]

# Порядок доменных групп в шапке (пункты без known-группы уходят в «Прочее» в конец).
NAV_GROUP_ORDER = ["Устройства", "Мониторинг", "Приложения", "Система", "Помощь"]

# Категории рабочего стола /apps (порядок разделов) и ядровые плитки (без манифестов).
APP_CATEGORY_ORDER = ["Утилиты", "Медиа", "Игры", "Система и сеть"]

CORE_APPS = [
    # (category, key, title, icon, order)
    ("Утилиты", "calc", "Калькулятор", "🔢", 10),
    ("Утилиты", "calendar", "Календарь", "📅", 20),
    ("Утилиты", "timer", "Таймер", "⏳", 30),
    ("Утилиты", "stopwatch", "Секундомер", "⏱", 40),
    ("Утилиты", "alarm", "Будильник", "⏰", 50),
    ("Игры", "snake", "Змейка", "🐍", 10),
    ("Игры", "tetris", "Тетрис", "🟦", 20),
    ("Игры", "g2048", "2048", "🟩", 30),
    ("Игры", "arkanoid", "Арканоид", "🟪", 40),
]

_manifest_cache = {"ts": 0.0, "data": None}
_state_cache = {"mtime": -1, "data": None}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        _state_cache["mtime"] = -1
        return True
    except Exception:
        return False


def load_state():
    try:
        mtime = os.path.getmtime(STATE_PATH)
    except Exception:
        mtime = -1
    if _state_cache["data"] is not None and _state_cache["mtime"] == mtime:
        return _state_cache["data"]
    data = _read_json(STATE_PATH, {})
    _state_cache["data"] = data
    _state_cache["mtime"] = mtime
    return data


def discover_modules(force=False):
    now = time.time()
    if not force and _manifest_cache["data"] is not None and now - _manifest_cache["ts"] < 5:
        return _manifest_cache["data"]
    mods = []
    for path in sorted(glob.glob(os.path.join(MODULES_DIR, "*", "module.json"))):
        m = _read_json(path, None)
        if not isinstance(m, dict) or not m.get("id"):
            continue
        m["_dir"] = os.path.dirname(path)
        mods.append(m)
    _manifest_cache["data"] = mods
    _manifest_cache["ts"] = now
    return mods


def get_module(mid):
    for m in discover_modules():
        if m["id"] == mid:
            return m
    return None


def module_status(mid):
    """(installed, enabled). Дефолт для builtin-модулей — True/True."""
    m = get_module(mid)
    builtin = bool(m and m.get("builtin"))
    entry = load_state().get(mid) or {}
    installed = entry.get("installed", builtin)
    enabled = entry.get("enabled", builtin)
    return bool(installed), bool(enabled)


def set_module_status(mid, installed=None, enabled=None):
    state = load_state()
    entry = state.get(mid) or {}
    if installed is not None:
        entry["installed"] = bool(installed)
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    if not entry:
        return False
    state[mid] = entry
    return save_state(state)


def record_install_result(mid, result):
    state = load_state()
    entry = state.get(mid) or {}
    entry["installed"] = True
    entry["last"] = result
    state[mid] = entry
    save_state(state)


# ==================== STEP 8: вычисляемые статусы модулей ====================

MODULE_STATUSES = (
    "error", "incompatible", "requires-hardware", "requires-dependency",
    "disabled", "active", "available", "unknown",
)

_pkg_cache = {"installed": None, "ts": 0}


def _missing_apt_packages(pkgs):
    """Отсутствующие apt-пакеты одним dpkg-query (кэш 60 с).

    Не смогли проверить (нет dpkg/timeout) → [] — не считаем зависимость
    отсутствующей (ничего не угадываем).
    """
    pkgs = sorted(set(pkgs or []))
    if not pkgs:
        return []
    now = time.time()
    if _pkg_cache["installed"] is None or now - _pkg_cache["ts"] >= 60:
        installed = set()
        try:
            r = process.run(
                ["dpkg-query", "-W", "-f", "${Package} ${Status}\n"] + pkgs,
                timeout=15,
            )
            for line in (r.stdout or "").splitlines():
                parts = line.rsplit(" ", 3)
                if len(parts) == 4 and parts[1] == "install" and parts[2] == "ok":
                    installed.add(parts[0])
        except Exception:
            return []
        _pkg_cache["installed"] = installed
        _pkg_cache["ts"] = now
    return [p for p in pkgs if p not in _pkg_cache["installed"]]


def status_context():
    """Контекст для compute_status: архитектура + capabilities + APP_VERSION."""
    from core import capabilities
    caps = capabilities.collect()
    try:
        from core.version import APP_VERSION
        app_version = str(APP_VERSION)
    except Exception:
        app_version = ""
    return {"arch": (caps.get("board") or {}).get("arch"), "caps": caps,
            "app_version": app_version}


def compute_status(m, entry, ctx, missing_pkgs=None):
    """Вычисляемый статус модуля (STEP 8). Чистая функция (тестируется).

    Приоритет: incompatible → requires-hardware → requires-dependency →
    error → disabled → active → available; исключение/битый манифест →
    unknown. missing_pkgs — множество отсутствующих apt-пакетов (общий
    dpkg-batch из modules_with_status).

    Манифест 2.0 (все чеки опциональны — старые манифесты ведут себя
    как раньше; ctx без app_version — проверку версии пропускаем):
    min_core_version/max_core_version → incompatible (fail-closed на
    нечитаемых строках версии); capabilities "group.key" → requires-hardware
    (только item-level; голые группы не проверяем — не гадаем);
    dependencies (id модулей) → requires-dependency, пока хоть одна
    зависимость не установлена/выключена.
    """
    try:
        if not isinstance(m, dict) or not m.get("id"):
            return "unknown"

        if m.get("min_core_version") or m.get("max_core_version"):
            app_ver = ctx.get("app_version")
            if app_ver:
                ok, _why = manifest_mod.core_version_ok(
                    m.get("min_core_version"), m.get("max_core_version"), app_ver)
                if not ok:
                    return "incompatible"

        hw = m.get("hardware") or {}
        if not isinstance(hw, dict):
            hw = {}

        arch_list = hw.get("arch") or []
        if arch_list and ctx.get("arch") not in arch_list:
            return "incompatible"

        caps = ctx.get("caps") or {}
        tools = caps.get("tools") or {}
        for t in hw.get("tools") or []:
            c = tools.get(t)
            if not c or c.get("state") != "present":
                return "requires-hardware"
        storage = caps.get("storage") or {}
        for s in hw.get("storage") or []:
            c = storage.get(s)
            if not c or c.get("state") != "present":
                return "requires-hardware"
        for key in m.get("capabilities") or []:
            if not isinstance(key, str) or "." not in key:
                continue  # группа целиком / битый ключ — не гадаем
            grp, item = key.split(".", 1)
            g = caps.get(grp)
            c = g.get(item) if isinstance(g, dict) else None
            if not isinstance(c, dict) or c.get("state") != "present":
                return "requires-hardware"

        deps = m.get("deps") or {}
        if missing_pkgs:
            my_pkgs = deps.get("apt") or []
            if any(p in missing_pkgs for p in my_pkgs):
                return "requires-dependency"
        for d in deps.get("dirs") or []:
            if not os.path.isdir(d):
                return "requires-dependency"
        for dep in m.get("dependencies") or []:
            if not isinstance(dep, str) or not dep:
                continue
            if get_module(dep) is None:
                return "requires-dependency"
            d_installed, d_enabled = module_status(dep)
            if not (d_installed and d_enabled):
                return "requires-dependency"

        last = (entry or {}).get("last")
        if isinstance(last, dict) and last.get("ok") is False:
            return "error"

        builtin = bool(m.get("builtin"))
        installed = bool((entry or {}).get("installed", builtin))
        enabled = bool((entry or {}).get("enabled", builtin))
        if not installed:
            return "available"
        if not enabled:
            return "disabled"
        return "active"
    except Exception:
        return "unknown"


def modules_with_status():
    """Все модули + вычисляемый статус (для /modules): одним dpkg-batch."""
    state = load_state()
    mods = discover_modules()
    all_pkgs = []
    for m in mods:
        all_pkgs += ((m.get("deps") or {}).get("apt") or [])
    missing = set(_missing_apt_packages(all_pkgs))
    ctx = status_context()
    rows = []
    for m in mods:
        entry = state.get(m["id"]) or {}
        rows.append({
            "m": m,
            "installed": bool(entry.get("installed", bool(m.get("builtin")))),
            "enabled": bool(entry.get("enabled", bool(m.get("builtin")))),
            "last": entry.get("last"),
            "status": compute_status(m, entry, ctx, missing),
        })
    return rows


def nav_items():
    items = list(CORE_NAV)
    for m in discover_modules():
        installed, enabled = module_status(m["id"])
        tab = m.get("tab")
        if installed and enabled and isinstance(tab, dict) and tab.get("title"):
            items.append({
                "page": m.get("page") or m["id"],
                "title": tab["title"],
                "url": m.get("url", "/"),
                "order": int(tab.get("order", 500)),
                "group": tab.get("group") or "Прочее",
                "module": m["id"],
            })
    return sorted(items, key=lambda x: x["order"])


def nav_groups(admin=False):
    """Доменные группы навигации: [{name, entries}] (порядок — NAV_GROUP_ORDER).

    admin=False скрывает пункты с флагом admin (например, «Модули»).
    Ключ entries, а не items: в Jinja словарь с ключом "items" конфликтует
    с методом dict.items (attempts lookup атрибута первым).
    """
    buckets = {}
    for it in nav_items():
        if it.get("admin") and not admin:
            continue
        buckets.setdefault(it.get("group") or "Прочее", []).append(it)
    out = [{"name": name, "entries": buckets.pop(name)}
           for name in NAV_GROUP_ORDER if buckets.get(name)]
    out += [{"name": name, "entries": entries} for name, entries in sorted(buckets.items())]
    return out


def active_page(path):
    """Ключ активной вкладки по пути запроса ('' — ничего)."""
    if path == "/modules":
        return "modules"
    for it in nav_items():
        url = it["url"]
        if url == "/":
            if path == "/":
                return it["page"]
        elif path == url or path.startswith(url + "/"):
            return it["page"]
    return ""


def disabled_prefixes():
    """URL-префиксы выключенных (но установленных) модулей — для before_request."""
    out = []
    for m in discover_modules():
        installed, enabled = module_status(m["id"])
        if installed and not enabled:
            prefixes = m.get("prefixes")
            if not prefixes:
                u = m.get("url")
                prefixes = [u] if u else []
            for p in prefixes:
                if p and p not in out:
                    out.append(p)
    return out


def app_items():
    """Плитки приложений из включённых модулей (поле app в манифесте)."""
    out = []
    for m in discover_modules():
        installed, enabled = module_status(m["id"])
        app = m.get("app")
        if installed and enabled and isinstance(app, dict) and app.get("title"):
            out.append({
                "key": app.get("key") or m["id"],
                "title": app["title"],
                "icon": app.get("icon", "📦"),
                "category": app.get("category", "Прочее"),
                "order": int(app.get("order", 500)),
                "description": m.get("description", ""),
                "module": m["id"],
            })
    return out


def block_items(slot=None):
    """Блоки включённых модулей для слота во вкладке (поле block в манифесте).

    Манифест блока:
        "type": "block",
        "block": {"slot": "currencies", "order": 10, "title": "Fiat валюты",
                  "template": "<id>/block.html"}   # template опционален
    Возвращает [{id, title, order, slot, module, template}] отсортированное по order.
    """
    out = []
    for m in discover_modules():
        installed, enabled = module_status(m["id"])
        blk = m.get("block")
        if not (installed and enabled and isinstance(blk, dict)):
            continue
        s = blk.get("slot")
        if slot is not None and s != slot:
            continue
        out.append({
            "id": m["id"],
            "title": blk.get("title") or m.get("name") or m["id"],
            "order": int(blk.get("order", 500)),
            "slot": s,
            "module": m["id"],
            "template": blk.get("template") or (m["id"] + "/block.html"),
        })
    return sorted(out, key=lambda x: x["order"])


def desktop_categories():
    """Разделы рабочего стола /apps: ядровые плитки + плитки включённых модулей."""
    cats = {name: [] for name in APP_CATEGORY_ORDER}
    for cat, key, title, icon, order in CORE_APPS:
        cats.setdefault(cat, []).append(
            {"key": key, "title": title, "icon": icon, "order": order,
             "description": "", "module": None})
    for it in app_items():
        cats.setdefault(it["category"], []).append(it)
    out = []
    for name, items in cats.items():
        if items:
            out.append({"name": name, "tiles": sorted(items, key=lambda x: x["order"])})
    return out


def help_sections():
    """Секции справки включённых модулей: [{id, title, html}]. help.md → markdown."""
    try:
        import markdown as _markdown
    except Exception:
        _markdown = None
    out = []
    for m in discover_modules():
        installed, enabled = module_status(m["id"])
        if not (installed and enabled and m.get("help")):
            continue
        path = os.path.join(m["_dir"], "help.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if _markdown:
            html = _markdown.markdown(text, extensions=["extra"])
        else:
            esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html = "<pre>" + esc + "</pre>"
        out.append({"id": m["id"], "title": m.get("name") or m["id"], "html": html})
    return out

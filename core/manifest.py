"""Манифест модуля 2.0: сетка прав, валидация, версии (спека §10–12).

Поля v2 (добавляются поверх манифеста 1.1, старые манифесты валидны):
    capabilities   — ["group"] / ["group.key"] — требования к core/capabilities
    dependencies   — ["<module-id>"] — модули, которые должны быть включены
    conflicts      — ["<module-id>"] — модули, несовместимые одновременно
    services       — ["<service>"] — системные сервисы, которые использует модуль
    configuration  — {} — схема настроек модуля (контракт для UI/SDR)
    role_support   — ["<role-id>"] — роли, включающие этот модуль (Roles 2.0)
    publisher      — издатель модуля (identity; сверяется с index.json)
    min_core_version / max_core_version — совместимость с APP_VERSION
    sha256         — НЕ поле module.json: контрольная сумма тарболла живёт
                     в index.json (манифест не может верифицировать сам себя).

Сетка прав (спека §11) — замкнутый список; permissions в манифесте
только из неё (v1-примеры «admin/network» не использовались ни в одном
из 33 существующих манифестов). Права — контракт доверия для UI
(запрос при установке), sandbox в 2.0-4 не вводится (спека §11).
"""
import re

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}$")
_CAP_KEY_RE = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)?$")
_VER_RE = re.compile(r"\d+(\.\d+){0,3}$")

# Ключ → (подпись для UI, описание). Порядок = порядок показа в запросе прав.
PERMISSIONS_GRID = (
    ("network.read", "Сеть: чтение",
     "Чтение состояния сетевых интерфейсов и подключений"),
    ("network.configure", "Сеть: настройка",
     "Изменение интерфейсов, маршрутов и DNS — может разорвать текущее подключение"),
    ("storage.read", "Накопители: чтение",
     "Доступ к файлам и данным на дисках"),
    ("storage.write", "Накопители: запись",
     "Создание и изменение файлов на дисках"),
    ("services.read", "Сервисы: чтение",
     "Чтение состояния системных служб"),
    ("services.control", "Сервисы: управление",
     "Запуск, остановка и перезапуск системных служб"),
    ("process.execute", "Процессы: запуск",
     "Запуск системных команд и фоновых процессов"),
    ("camera.read", "Камеры: чтение",
     "Просмотр видео и снимков с камер"),
    ("camera.control", "Камеры: управление",
     "Настройка и управление камерами"),
    ("usb.access", "USB: доступ",
     "Доступ к USB-устройствам"),
    ("gpio.access", "GPIO: доступ",
     "Управление GPIO-пинами"),
    ("serial.access", "Serial: доступ",
     "Доступ к последовательным портам (UART)"),
)

PERMISSION_KEYS = tuple(k for k, _, _ in PERMISSIONS_GRID)
_LABELS = {k: (label, desc) for k, label, desc in PERMISSIONS_GRID}


def permission_info(key):
    """(подпись, описание) для ключа права; неизвестный ключ → None."""
    return _LABELS.get(key)


def _as_str_list(v):
    """v → список непустых строк (не-списки/мусор → None = невалидно)."""
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        return None
    return v


def parse_version(s):
    """'1.2.3' → (1, 2, 3); неразборчивая/не-строка → None."""
    if not isinstance(s, str) or not _VER_RE.match(s.strip()):
        return None
    return tuple(int(p) for p in s.strip().split("."))


def core_version_ok(min_v, max_v, current):
    """(ok, причина) совместимости current с [min_v, max_v].

    Пустые min/max — без ограничения. Неразборчивая заявленная версия →
    несовместимо (не можем проверить → несовместим, fail-closed).
    Неразборчивый current (не наш APP_VERSION) → пропускаем проверку (не
    гадаем о текущей системе). None/пусто в min/max снимается как ok.
    """
    cur = parse_version(current)
    if min_v:
        lo = parse_version(min_v)
        if lo is None:
            return False, "нечитаемая min_core_version: %r" % (min_v,)
        if cur is None:
            return True, ""
        # сравнение по общему числу частей (1.2 ~ 1.2.0)
        n = max(len(lo), len(cur))
        if tuple(cur) + (0,) * (n - len(cur)) < lo + (0,) * (n - len(lo)):
            return False, "модуль требует ядро ≥ %s (сейчас %s)" % (min_v, current)
    if max_v:
        hi = parse_version(max_v)
        if hi is None:
            return False, "нечитаемая max_core_version: %r" % (max_v,)
        if cur is None:
            return True, ""
        n = max(len(hi), len(cur))
        if tuple(cur) + (0,) * (n - len(cur)) > hi + (0,) * (n - len(hi)):
            return False, "модуль для ядра ≤ %s (сейчас %s)" % (max_v, current)
    return True, ""


def validate_manifest(m):
    """Список ошибок структуры манифеста (пустой [] = валиден).

    v1-поля не обязательны (старые манифесты валидны); проверяются только
    присутствующие поля. Вызывается при установке из каталога (fail-closed)
    и в тестах; локальные манифесты на discover не валидируются — не ломаем
    рабочие каталоги опечатками.
    """
    if not isinstance(m, dict):
        return ["манифест не объект JSON"]
    errs = []
    mid = m.get("id")
    if not isinstance(mid, str) or not _ID_RE.match(mid):
        errs.append("id: нужна латиница [a-z0-9_-] без спецсимволов (получено %r)" % (mid,))
    for fld in ("name", "description", "publisher"):
        if fld in m and not isinstance(m[fld], str):
            errs.append("%s: ожидается строка" % fld)
    for fld in ("version", "min_core_version", "max_core_version"):
        if fld in m and m[fld] not in (None, ""):
            if parse_version(m[fld]) is None:
                errs.append("%s: ожидается версия вида 1.2.3 (получено %r)"
                            % (fld, m[fld]))
    perms = m.get("permissions")
    if perms is not None:
        pl = _as_str_list(perms)
        if pl is None:
            errs.append("permissions: ожидается список строк")
        else:
            for p in pl:
                if p not in PERMISSION_KEYS:
                    errs.append(
                        "permissions: неизвестное право %r (допустимо: %s)"
                        % (p, ", ".join(PERMISSION_KEYS)))
    for fld in ("capabilities", "dependencies", "conflicts",
                "services", "role_support"):
        if fld in m and m[fld] is not None:
            fl = _as_str_list(m[fld])
            if fl is None:
                errs.append("%s: ожидается список строк" % fld)
            elif fld == "capabilities":
                for c in fl:
                    if not _CAP_KEY_RE.match(c):
                        errs.append("capabilities: ключ %r должен быть "
                                    "group или group.key" % c)
    if "configuration" in m and m["configuration"] is not None:
        if not isinstance(m["configuration"], dict):
            errs.append("configuration: ожидается объект")
    if "hardware" in m and m["hardware"] is not None:
        if not isinstance(m["hardware"], dict):
            errs.append("hardware: ожидается объект")
    if "deps" in m and m["deps"] is not None:
        if not isinstance(m["deps"], dict):
            errs.append("deps: ожидается объект")
    return errs


def install_confirm_text(m):
    """Текст confirm() для UI-запроса прав при установке (спека §11).

    Без прав → "" (форма уходит без confirm). Неизвестные ключи показываем
    как есть — не скрываем от администратора.
    """
    perms = _as_str_list((m or {}).get("permissions") or [])
    if not perms:
        return ""
    name = (m.get("name") or m.get("id") or "").strip() or "Модуль"
    lines = ["Модуль «%s» запрашивает права:" % name]
    for p in perms:
        info = permission_info(p)
        if info:
            lines.append("  • %s — %s" % (info[0], p))
        else:
            lines.append("  • %s (неизвестное право)" % p)
    lines.append("")
    lines.append("Продолжить?")
    return "\n".join(lines)

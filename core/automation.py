# -*- coding: utf-8 -*-
"""Automation engine (PHASE 2.0-13, спека §17): Event → Rule → Action.

Компактный appliance engine, НЕ Home Assistant clone (§17):

- правило: ``{id, name, enabled, event, actions[], cooldown_sec}``;
- источник событий — ``core.events.subscribe`` (namespace §16);
- матчинг: enabled + событие (dual-read: алиасы legacy⇄namespace —
  правило ``OFFLINE`` триггерит и ``device.offline``) + cooldown;
- действия — реестр ``register_action(type, fn)``; встроены ``log``
  (запись в лог панели) и ``event`` (новое namespace-событие);
- защита от петель: события, порождённые automation (metadata
  ``automation``), правила НЕ триггерируют — цепочки вне объёма (§17:
  не HA-клон); расширение действий модулями — через register_action.
"""
import json
import logging
import time

log = logging.getLogger("lan-discovery")

AUTOMATION_DDL = (
    """CREATE TABLE IF NOT EXISTS automation_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        event TEXT NOT NULL,
        actions TEXT,
        cooldown_sec INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        fired_count INTEGER NOT NULL DEFAULT 0,
        last_fired_at TEXT
    )""",
)

_ACTIONS = {}
_last_fired = {}
_subscription = None


def ensure_automation_table(con):
    """DDL таблицы правил (паттерн JOBS_DDL; вызывает core.db)."""
    for stmt in AUTOMATION_DDL:
        con.execute(stmt)


# --- действия (реестр, §17) -------------------------------------------------

def register_action(action_type, fn):
    """Зарегистрировать обработчик действия: fn(rule, action, payload)."""
    _ACTIONS[action_type] = fn
    return fn


def _act_log(rule, action, payload):
    level = str(action.get("level", "info")).lower()
    fn = getattr(log, level, log.info)
    fn("AUTOMATION %s [%s]: trigger=%s ip=%s | %s",
       rule["name"], rule["event"], payload.get("name"),
       payload.get("ip"), action.get("message", ""))


def _act_event(rule, action, payload):
    from core import events as core_events
    name = action.get("event")
    if name not in core_events.NAMESPACE_EVENTS:
        log.warning("AUTOMATION %s: неизвестное имя события %r — пропуск",
                    rule["name"], name)
        return
    meta = {"automation": rule["id"], "trigger": payload.get("name")}
    extra = action.get("metadata")
    if isinstance(extra, dict):
        meta.update(extra)
    core_events.emit(
        name,
        ip=action.get("ip", payload.get("ip")),
        hostname=action.get("hostname", payload.get("hostname")),
        source="automation",
        metadata=meta,
    )


register_action("log", _act_log)
register_action("event", _act_event)


def action_types():
    """Список зарегистрированных типов действий (для API/форм)."""
    return sorted(_ACTIONS)


# --- хранение правил --------------------------------------------------------

def rule_to_dict(row):
    """Строка automation_rules -> dict (actions парсится из JSON).

    row: id, name, enabled, event, actions, cooldown_sec, created_at,
    fired_count, last_fired_at (_RULE_COLS).
    """
    try:
        actions = json.loads(row[4]) if row[4] else []
    except Exception:
        actions = []
    return {
        "id": row[0],
        "name": row[1],
        "enabled": bool(row[2]),
        "event": row[3],
        "actions": actions,
        "cooldown_sec": row[5],
        "created_at": row[6],
        "fired_count": row[7],
        "last_fired_at": row[8],
    }


_RULE_COLS = ("id, name, enabled, event, actions, cooldown_sec, "
              "created_at, fired_count, last_fired_at")


def list_rules(con):
    """Все правила, новые первыми."""
    return [rule_to_dict(r) for r in con.execute(
        "SELECT %s FROM automation_rules ORDER BY id DESC" % _RULE_COLS
    ).fetchall()]


def get_rule(con, rule_id):
    row = con.execute(
        "SELECT %s FROM automation_rules WHERE id=?" % _RULE_COLS,
        (rule_id,),
    ).fetchone()
    return rule_to_dict(row) if row else None


def validate_rule(data):
    """Проверка правила -> (rule|None, error|None)."""
    from core import events as core_events
    name = (data.get("name") or "").strip()
    event = (data.get("event") or "").strip()
    actions = data.get("actions")
    if not name:
        return None, "не задано имя"
    if event not in core_events.NAMESPACE_EVENTS and \
            event not in core_events.LEGACY_ALIASES:
        return None, f"неизвестное имя события: {event!r}"
    if not isinstance(actions, list) or not actions:
        return None, "нужен непустой список actions"
    for act in actions:
        if not isinstance(act, dict) or not act.get("type"):
            return None, "каждый action — dict с type"
        if act["type"] not in _ACTIONS:
            return None, f"неизвестный тип действия: {act['type']!r}"
        if act["type"] == "event":
            target = act.get("event")
            if target not in core_events.NAMESPACE_EVENTS:
                return None, "неизвестное имя события в action: %r" % target
    try:
        cooldown = int(data.get("cooldown_sec") or 0)
    except (TypeError, ValueError):
        return None, "cooldown_sec — целое число"
    if cooldown < 0:
        return None, "cooldown_sec не может быть отрицательным"
    return {
        "name": name,
        "enabled": 1 if data.get("enabled", True) else 0,
        "event": event,
        "actions": json.dumps(actions, ensure_ascii=False),
        "cooldown_sec": cooldown,
    }, None


def add_rule(con, data):
    """Создать правило (validate_rule внутри); возвращает id или None."""
    rule, err = validate_rule(data)
    if err:
        return None, err
    from core.events import now_ts
    cur = con.execute(
        "INSERT INTO automation_rules "
        "(name, enabled, event, actions, cooldown_sec, created_at) "
        "VALUES (:name, :enabled, :event, :actions, :cooldown_sec, "
        ":created_at)",
        {**rule, "created_at": now_ts()},
    )
    con.commit()
    return cur.lastrowid, None


def delete_rule(con, rule_id):
    """Удалить правило; True если строка была."""
    cur = con.execute("DELETE FROM automation_rules WHERE id=?",
                      (rule_id,))
    con.commit()
    _last_fired.pop(rule_id, None)
    return cur.rowcount > 0


def set_enabled(con, rule_id, enabled):
    cur = con.execute(
        "UPDATE automation_rules SET enabled=? WHERE id=?",
        (1 if enabled else 0, rule_id),
    )
    con.commit()
    return cur.rowcount > 0


# --- engine: событие → правило → действия -----------------------------------

def match_event(rule_event, payload_name):
    """Dual-read матчинг (как фильтр list_events): legacy ⇄ namespace."""
    from core.events import _event_variants
    return payload_name in _event_variants(rule_event)


def _cooldown_ok(rule, now):
    sec = int(rule.get("cooldown_sec") or 0)
    if sec <= 0:
        return True
    last = _last_fired.get(rule["id"])
    return last is None or (now - last) >= sec


def run_rule(rule, payload):
    """Выполнить действия правила; ошибки действий не роняют engine."""
    for action in rule.get("actions") or []:
        handler = _ACTIONS.get(action.get("type"))
        if handler is None:
            log.warning("AUTOMATION %s: нет обработчика %r",
                        rule["name"], action.get("type"))
            continue
        try:
            handler(rule, action, payload)
        except Exception as e:
            log.error("AUTOMATION %s action %s: %s",
                      rule["name"], action.get("type"), e)


def handle_event(payload):
    """Подписчик core.events: событие → подходящие правила (§17)."""
    meta = payload.get("metadata")
    if isinstance(meta, dict) and meta.get("automation") is not None:
        return  # события automation не триггерят правила (защита петель)
    name = payload.get("name")
    if not name:
        return
    from core.db import get_db
    try:
        con = get_db()
    except Exception as e:
        log.error("AUTOMATION db open: %s", e)
        return
    try:
        rules = [r for r in list_rules(con)
                 if r["enabled"] and match_event(r["event"], name)]
        now = time.time()
        for rule in rules:
            if not _cooldown_ok(rule, now):
                continue
            _last_fired[rule["id"]] = now
            run_rule(rule, payload)
            try:
                con.execute(
                    "UPDATE automation_rules SET fired_count=fired_count+1, "
                    "last_fired_at=? WHERE id=?",
                    (payload.get("timestamp"), rule["id"]),
                )
                con.commit()
            except Exception as e:
                log.error("AUTOMATION persist fired %s: %s",
                          rule["id"], e)
    except Exception as e:
        log.error("AUTOMATION handle %s: %s", name, e)
    finally:
        con.close()


def start():
    """Подписаться на события (идемпотентно); вызывается из app."""
    global _subscription
    if _subscription is not None:
        return False
    from core import events as core_events
    _subscription = core_events.subscribe(handle_event)
    log.info("AUTOMATION: started (subscribe events)")
    return True


def stop():
    """Отписаться (идемпотентно)."""
    global _subscription
    if _subscription is None:
        return False
    _subscription()
    _subscription = None
    log.info("AUTOMATION: stopped")
    return True

# -*- coding: utf-8 -*-
"""API правил Automation (PHASE 2.0-13, спека §17).

Минимальный CRUD для appliance engine: список/создание/toggle/удаление.
Действия правил — реестр core.automation (log/event, расширяется модулями).
"""
from flask import jsonify, render_template, request


def register_routes(app):
    from core import automation
    from core.db import get_db
    from modules.auth import login_required, can_edit

    @app.route("/automation")
    @login_required
    def automation_page():
        from core import events as core_events
        con = get_db()
        try:
            rules = automation.list_rules(con)
        finally:
            con.close()
        return render_template(
            "automation.html",
            rules=rules,
            ns_events=sorted(core_events.NAMESPACE_EVENTS),
            legacy_events=sorted(core_events.LEGACY_ALIASES),
            actions=automation.action_types(),
        )

    @app.route("/api/automation/rules")
    @login_required
    def automation_rules_list():
        con = get_db()
        try:
            rules = automation.list_rules(con)
        finally:
            con.close()
        return jsonify({"ok": True, "rules": rules,
                        "actions": automation.action_types()})

    @app.route("/api/automation/rules", methods=["POST"])
    @can_edit
    @login_required
    def automation_rules_create():
        data = request.get_json(silent=True) or {}
        con = get_db()
        try:
            rule_id, err = automation.add_rule(con, data)
        finally:
            con.close()
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return jsonify({"ok": True, "id": rule_id}), 201

    @app.route("/api/automation/rules/<int:rule_id>/toggle",
               methods=["POST"])
    @can_edit
    @login_required
    def automation_rules_toggle(rule_id):
        data = request.get_json(silent=True) or {}
        con = get_db()
        try:
            enabled = data.get("enabled")
            if enabled is None:
                rule = automation.get_rule(con, rule_id)
                if rule is None:
                    return jsonify({"ok": False,
                                    "error": "правило не найдено"}), 404
                enabled = not rule["enabled"]
            ok = automation.set_enabled(con, rule_id, enabled)
        finally:
            con.close()
        if not ok:
            return jsonify({"ok": False,
                            "error": "правило не найдено"}), 404
        return jsonify({"ok": True, "enabled": bool(enabled)})

    @app.route("/api/automation/rules/<int:rule_id>", methods=["DELETE"])
    @can_edit
    @login_required
    def automation_rules_delete(rule_id):
        con = get_db()
        try:
            ok = automation.delete_rule(con, rule_id)
        finally:
            con.close()
        if not ok:
            return jsonify({"ok": False,
                            "error": "правило не найдено"}), 404
        return jsonify({"ok": True})

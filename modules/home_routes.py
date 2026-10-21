# -*- coding: utf-8 -*-
"""Дом/сигнализация — маршруты модуля home."""
from flask import render_template, request, jsonify

from modules import home as engine


def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    admin_required = ctx.admin_required
    page_data = ctx.page_data

    engine.init_home_db()

    @app.route("/home")
    @login_required
    def home_alarm_page():
        data = page_data()
        data["home"] = engine.status()
        u = ctx.get_current_user()
        data["is_admin"] = bool(u and u.role == "admin")
        data["error"] = request.args.get("error") or None
        return render_template("home.html", **data)

    @app.route("/api/home/status")
    @login_required
    def home_status():
        return jsonify(engine.status())

    # ------------------------------------------------------------ alarm

    @app.route("/api/home/arm", methods=["POST"])
    @can_edit
    @login_required
    def home_arm():
        try:
            return jsonify({"ok": True, "state": engine.arm()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/home/disarm", methods=["POST"])
    @can_edit
    @login_required
    def home_disarm():
        try:
            return jsonify({"ok": True, "state": engine.disarm()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/home/siren", methods=["POST"])
    @can_edit
    @login_required
    def home_siren():
        data = request.get_json(silent=True) or {}
        try:
            ok = engine.set_siren(bool(data.get("on")))
            return jsonify({"ok": ok})
        except (RuntimeError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # ------------------------------------------------------------- zones

    @app.route("/api/home/zones", methods=["POST"])
    @can_edit
    @login_required
    def home_zone_add():
        data = request.get_json(silent=True) or {}
        try:
            zid = engine.add_zone(data.get("name"), data.get("chip"),
                                  data.get("line"),
                                  data.get("trigger_level") or 0)
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "id": zid})

    @app.route("/api/home/zones/<int:zid>", methods=["DELETE"])
    @can_edit
    @login_required
    def home_zone_delete(zid):
        ok = engine.delete_zone(zid)
        return jsonify({"ok": ok}), 200 if ok else 404

    @app.route("/api/home/zones/<int:zid>/test", methods=["POST"])
    @login_required
    def home_zone_test(zid):
        zones = engine.list_zones()
        for z in zones:
            if z["id"] == zid:
                return jsonify({"ok": True, "value": z["value"],
                                "open": z["open"]})
        return jsonify({"ok": False, "error": "зона не найдена"}), 404

    @app.route("/api/home/siren_zone", methods=["POST"])
    @can_edit
    @login_required
    def home_siren_zone():
        data = request.get_json(silent=True) or {}
        chip = (data.get("chip") or "").strip()
        try:
            engine.set_siren_zone(chip or None, data.get("line") or 0)
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    # -------------------------------------------------------------- mqtt

    @app.route("/api/home/mqtt", methods=["POST"])
    @can_edit
    @login_required
    def home_mqtt_save():
        data = request.get_json(silent=True) or {}
        try:
            engine.mqtt_save(data.get("host"), data.get("port") or 1883,
                             data.get("prefix"), bool(data.get("enabled")))
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "mqtt": engine.mqtt_config()})

    @app.route("/api/home/mqtt/test", methods=["POST"])
    @can_edit
    @login_required
    def home_mqtt_test():
        try:
            engine.mqtt_test()
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    # ------------------------------------------------------------ install

    @app.route("/api/home/install", methods=["POST"])
    @admin_required
    @login_required
    def home_install():
        data = request.get_json(silent=True) or {}
        try:
            engine.install(data.get("pkg") or "")
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/home/install/status")
    @login_required
    def home_install_status():
        return jsonify(engine.install_status())

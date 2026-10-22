# -*- coding: utf-8 -*-
"""Спасательный AP — маршруты модуля rescue."""
from flask import (render_template, redirect, url_for, request,
                   jsonify)

from modules import rescue as engine


def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    admin_required = ctx.admin_required
    page_data = ctx.page_data

    def _page(error=None):
        data = page_data()
        data["st"] = engine.status()
        data["is_admin"] = bool(ctx.get_current_user and
                                ctx.get_current_user() and
                                ctx.get_current_user().role == "admin")
        if error:
            data["error"] = error
        return render_template("rescue.html", **data)

    @app.route("/rescue")
    @login_required
    def rescue_page():
        return _page(error=request.args.get("error") or None)

    @app.route("/api/rescue/status")
    @login_required
    def rescue_status():
        return jsonify(engine.status())

    @app.route("/api/rescue/config", methods=["POST"])
    @can_edit
    @login_required
    def rescue_config():
        data = request.get_json(silent=True) or {}
        try:
            cfg = engine.save_config(**data)
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "config": {
            k: v for k, v in cfg.items() if k != "passphrase"}})

    @app.route("/api/rescue/start", methods=["POST"])
    @can_edit
    @login_required
    def rescue_start():
        data = request.get_json(silent=True) or {}
        try:
            st = engine.start(data.get("iface") or None)
        except (RuntimeError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "status": st})

    @app.route("/api/rescue/stop", methods=["POST"])
    @can_edit
    @login_required
    def rescue_stop():
        return jsonify({"ok": True, "status": engine.stop()})

    # ------------------------------------------------------------- install

    @app.route("/api/rescue/install", methods=["POST"])
    @admin_required
    @login_required
    def rescue_install():
        data = request.get_json(silent=True) or {}
        try:
            engine.install(data.get("pkg") or "")
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/rescue/install/status")
    @login_required
    def rescue_install_status():
        return jsonify(engine.install_status())

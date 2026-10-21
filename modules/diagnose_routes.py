# -*- coding: utf-8 -*-
"""Диагностика «Что сломано?» — маршруты модуля diagnose."""
from flask import (render_template, redirect, url_for, request,
                   jsonify)

from modules import diagnose as engine


def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    page_data = ctx.page_data

    @app.route("/diagnose")
    @login_required
    def diagnose_page():
        data = page_data()
        data["diag"] = engine.status()
        data["error"] = request.args.get("error") or None
        return render_template("diagnose.html", **data)

    @app.route("/diagnose/run", methods=["POST"])
    @can_edit
    @login_required
    def diagnose_run():
        want_speed = request.form.get("speed") != "0"
        try:
            engine.start(want_speed=want_speed)
        except RuntimeError as e:
            return redirect(url_for("diagnose_page", error=str(e)))
        return redirect(url_for("diagnose_page"))

    @app.route("/api/diagnose/run", methods=["POST"])
    @can_edit
    @login_required
    def diagnose_run_api():
        payload = request.get_json(silent=True) or {}
        want_speed = payload.get("speed", True)
        try:
            engine.start(want_speed=bool(want_speed))
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/diagnose/status")
    @login_required
    def diagnose_status():
        return jsonify(engine.status())

# -*- coding: utf-8 -*-
"""Hardware-инструменты (Flipper) — маршруты модуля flipper."""
from flask import (render_template, redirect, url_for, request,
                   jsonify)

from modules import flipper as engine


def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    admin_required = ctx.admin_required
    page_data = ctx.page_data

    def _page(error=None):
        data = page_data()
        data["det"] = engine.detect()
        data["pager"] = engine.pager_status()
        data["ser"] = engine.serial_status()
        data["prog"] = engine.prog_status()
        data["install"] = engine.install_status()
        data["install_log"] = engine.install_log_tail(30)
        data["bauds"] = engine.SERIAL_BAUDS
        data["avr_programmers"] = engine.AVR_PROGRAMMERS
        data["avr_mcus"] = engine.AVR_MCUS
        data["esptool_chips"] = engine.ESPTOOL_CHIPS
        data["stm32_bauds"] = engine.STM32_BAUDS
        data["is_admin"] = bool(ctx.get_current_user and
                                ctx.get_current_user() and
                                ctx.get_current_user().role == "admin")
        if error:
            data["error"] = error
        return render_template("flipper.html", **data)

    @app.route("/flipper")
    @login_required
    def flipper_page():
        return _page(error=request.args.get("error") or None)

    @app.route("/api/flipper/detect")
    @login_required
    def flipper_detect():
        return jsonify(engine.detect())

    # ------------------------------------------------------------ install

    @app.route("/api/flipper/install", methods=["POST"])
    @admin_required
    @login_required
    def flipper_install():
        data = request.get_json(silent=True) or {}
        try:
            engine.install(data.get("pkg") or "")
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/install/status")
    @login_required
    def flipper_install_status():
        st = engine.install_status()
        st["log"] = engine.install_log_tail(30)
        return jsonify(st)

    # -------------------------------------------------------------- pager

    @app.route("/api/flipper/pager/status")
    @login_required
    def flipper_pager_status():
        return jsonify(engine.pager_status())

    @app.route("/api/flipper/pager/messages")
    @login_required
    def flipper_pager_messages():
        try:
            after = int(request.args.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        return jsonify({"ok": True, "messages": engine.pager_messages(after),
                        "status": engine.pager_status()})

    @app.route("/api/flipper/pager/start", methods=["POST"])
    @can_edit
    @login_required
    def flipper_pager_start():
        try:
            engine.pager_start()
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/pager/stop", methods=["POST"])
    @can_edit
    @login_required
    def flipper_pager_stop():
        engine.pager_stop()
        return jsonify({"ok": True})

    # ------------------------------------------------------------- serial

    @app.route("/api/flipper/serial/status")
    @login_required
    def flipper_serial_status():
        st = engine.serial_status()
        st["ports"] = engine.list_serial()
        return jsonify(st)

    @app.route("/api/flipper/serial/read")
    @login_required
    def flipper_serial_read():
        try:
            after = int(request.args.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        return jsonify({"ok": True, "lines": engine.serial_read(after),
                        "status": engine.serial_status()})

    @app.route("/api/flipper/serial/open", methods=["POST"])
    @can_edit
    @login_required
    def flipper_serial_open():
        data = request.get_json(silent=True) or {}
        try:
            engine.serial_open(data.get("path") or "",
                               int(data.get("baud") or 115200))
        except (RuntimeError, ValueError, OSError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/serial/close", methods=["POST"])
    @can_edit
    @login_required
    def flipper_serial_close():
        engine.serial_close()
        return jsonify({"ok": True})

    @app.route("/api/flipper/serial/write", methods=["POST"])
    @can_edit
    @login_required
    def flipper_serial_write():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "")
        if len(text) > 4096:
            return jsonify({"ok": False, "error": "слишком длинная строка"}), 400
        try:
            engine.serial_write(text)
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    # ------------------------------------------------------- программаторы

    @app.route("/api/flipper/prog/status")
    @login_required
    def flipper_prog_status():
        return jsonify(engine.prog_status())

    @app.route("/api/flipper/prog/log")
    @login_required
    def flipper_prog_log():
        try:
            after = int(request.args.get("after") or 0)
        except (TypeError, ValueError):
            after = 0
        return jsonify({"ok": True, "lines": engine.prog_log(after),
                        "status": engine.prog_status()})

    @app.route("/api/flipper/prog/avrdude", methods=["POST"])
    @can_edit
    @login_required
    def flipper_prog_avrdude():
        fw = request.form.get("firmware") or ""
        try:
            engine.prog_avrdude(fw, request.form.get("programmer") or "",
                                request.form.get("mcu") or "",
                                request.form.get("port") or "",
                                request.form.get("baud") or None)
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/prog/esptool", methods=["POST"])
    @can_edit
    @login_required
    def flipper_prog_esptool():
        fw = request.form.get("firmware") or ""
        try:
            engine.prog_esptool(fw, request.form.get("port") or "",
                                request.form.get("chip") or "auto",
                                request.form.get("baud") or 460800,
                                request.form.get("action") or "write_flash",
                                request.form.get("address") or "0x0")
        except (RuntimeError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/prog/stm32flash", methods=["POST"])
    @can_edit
    @login_required
    def flipper_prog_stm32flash():
        fw = request.form.get("firmware") or ""
        try:
            engine.prog_stm32flash(fw, request.form.get("port") or "",
                                   request.form.get("baud") or 115200,
                                   request.form.get("action") or "write")
        except (RuntimeError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True})

    @app.route("/api/flipper/prog/upload", methods=["POST"])
    @can_edit
    @login_required
    def flipper_prog_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "файл не выбран"}), 400
        try:
            path, size = engine.save_upload(f.filename, f.stream)
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "path": path, "size": size,
                        "name": f.filename})

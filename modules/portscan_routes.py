# -*- coding: utf-8 -*-
"""Сканер открытых портов — маршруты модуля portscan."""
import os

from flask import (render_template, redirect, url_for, request,
                   jsonify, send_file, abort)

from modules import portscan as engine


def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    page_data = ctx.page_data
    _cfg = ctx._cfg

    def _page(error=None):
        data = page_data()
        data["files"] = engine.list_files()
        data["job"] = engine.status()
        data["storage"] = engine.storage_info()
        data["default_range"] = _cfg("network", "subnet", "192.168.3.0/24")
        data["default_ports"] = "top"
        if error:
            data["error"] = error
        return render_template("portscan.html", **data)

    @app.route("/portscan")
    @login_required
    def portscan_page():
        return _page(error=request.args.get("error") or None)

    @app.route("/portscan/start", methods=["POST"])
    @can_edit
    @login_required
    def portscan_start():
        range_spec = (request.form.get("range") or "").strip()
        ports_spec = (request.form.get("ports") or "top").strip()
        try:
            timeout = float(request.form.get("timeout") or 0.5)
        except (TypeError, ValueError):
            timeout = 0.5
        timeout = min(max(timeout, 0.1), 5.0)
        confirm = request.form.get("confirm") == "1"
        try:
            hosts = engine.parse_targets(range_spec)
            ports = engine.parse_ports(ports_spec)
            if len(hosts) * len(ports) > engine.BIG_JOB and not confirm:
                raise RuntimeError(
                    "большой объём: %d хостов × %d портов (> %d) — "
                    "включите подтверждение" % (len(hosts), len(ports),
                                                engine.BIG_JOB))
            engine.start(hosts, ports, timeout, range_spec, ports_spec)
        except Exception as e:
            return _page(error=str(e)), 400
        return redirect(url_for("portscan_page"))

    @app.route("/portscan/stop", methods=["POST"])
    @can_edit
    @login_required
    def portscan_stop():
        engine.stop()
        return redirect(url_for("portscan_page"))

    @app.route("/portscan/delete", methods=["POST"])
    @can_edit
    @login_required
    def portscan_delete():
        name = (request.form.get("name") or "").strip()
        path = engine._safe_name(name)
        if not path:
            return _page(error="файл не найден: %s" % name), 404
        try:
            os.unlink(path)
        except Exception as e:
            return _page(error="не удалось удалить: %s" % e), 400
        return redirect(url_for("portscan_page"))

    @app.route("/portscan/file/<name>")
    @login_required
    def portscan_file(name):
        path = engine._safe_name(name)
        if not path:
            abort(404)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(512 * 1024)
        except Exception as e:
            return _page(error="не удалось прочитать: %s" % e), 400
        data = page_data()
        data["file_name"] = name
        data["file_content"] = content
        data["truncated"] = len(content) == 512 * 1024
        return render_template("portscan_file.html", **data)

    @app.route("/portscan/download/<name>")
    @login_required
    def portscan_download(name):
        path = engine._safe_name(name)
        if not path:
            abort(404)
        return send_file(path, as_attachment=True, download_name=name,
                         mimetype="text/plain")

    @app.route("/api/portscan/status")
    @login_required
    def portscan_status():
        return jsonify(engine.status())

    @app.route("/api/portscan/files")
    @login_required
    def portscan_files():
        return jsonify({"ok": True, "files": engine.list_files(),
                        "storage": engine.storage_info()})

# -*- coding: utf-8 -*-
"""Jobs API (PHASE 2.0-3): фоновые задачи (ROADMAP: manager + UI-виджет).

Регистрирует:
  GET  /api/jobs             — последние задачи (limit, status) + active
  GET  /api/jobs/<job_id>    — одна задача (статус/результат для поллинга)
  POST /api/jobs/<job_id>/cancel — cooperative-отмена (admin)

Контракты аддитивны к 1.1 (новые URL). configure(db_path) — без I/O на
import app; схема/recover — лениво при первом обращении (core.jobs).
"""
import logging
import threading

from flask import jsonify, request

from core import jobs

log = logging.getLogger("lan-discovery")

_retention_started = False


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    from core.db import DB
    jobs.manager.configure(db_path=DB)

    global _retention_started
    if not _retention_started:
        _retention_started = True
        threading.Thread(target=jobs.retention_loop, daemon=True,
                         name="lan-jobs-retention").start()

    @app.route("/api/jobs")
    @login_required
    def api_jobs_list():
        try:
            limit = max(1, min(200, int(request.args.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        status = request.args.get("status") or None
        if status and status not in jobs.STATUSES:
            return jsonify({"ok": False,
                            "error": "status: одно из %s"
                                     % ", ".join(jobs.STATUSES)}), 400
        items = jobs.list(status=status, limit=limit)
        active = sum(1 for j in items
                     if j["status"] in ("queued", "running"))
        return jsonify({"ok": True, "jobs": items, "active": active})

    @app.route("/api/jobs/<job_id>")
    @login_required
    def api_jobs_get(job_id):
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"ok": False,
                            "error": "задача не найдена"}), 404
        return jsonify({"ok": True, "job": job})

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    @admin_required
    def api_jobs_cancel(job_id):
        res = jobs.cancel(job_id)
        if res is None:
            return jsonify({"ok": False,
                            "error": "задача не найдена"}), 404
        return jsonify(res), (200 if res.get("ok") else 409)

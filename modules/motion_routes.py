# -*- coding: utf-8 -*-
"""HTTP-API модуля motion (см. modules/motion/help.md).

Интерфейс UI отложен (встраивание в панель — после согласования); здесь
только API для статуса/событий/тестов и приём hook-событий камер.
"""
import hmac
import logging
import os

from flask import request, jsonify, send_file

log = logging.getLogger("lan-discovery")


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    from modules import motion_engine
    motion_engine.start()

    @app.route("/api/motion/status")
    @login_required
    def motion_status():
        return jsonify(motion_engine.status_dict())

    @app.route("/api/motion/events")
    @login_required
    def motion_events_list():
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100
        camera = (request.args.get("camera") or "").strip()
        from modules.devices_routes import get_db
        q = ("SELECT id, ts, epoch, camera_id, camera_name, score, "
             "detector, photo FROM motion_events")
        params = []
        if camera:
            q += " WHERE camera_id=?"
            params.append(int(camera) if camera.isdigit() else 0)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        con = get_db()
        try:
            rows = con.execute(q, params).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            out.append({
                "id": r[0], "ts": r[1], "epoch": r[2], "camera_id": r[3],
                "camera_name": r[4], "score": r[5], "detector": r[6],
                "photo": f"/api/motion/photo/{r[0]}" if r[7] else None,
            })
        return jsonify(out)

    @app.route("/api/motion/photo/<int:eid>")
    @login_required
    def motion_photo(eid):
        from modules.devices_routes import get_db
        con = get_db()
        try:
            row = con.execute(
                "SELECT photo FROM motion_events WHERE id=?", (eid,)
            ).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return jsonify({"error": "нет фото"}), 404
        path = os.path.realpath(row[0])
        root = os.path.realpath(motion_engine.MOTION_DIR)
        if path != root and not path.startswith(root + os.sep):
            return jsonify({"error": "bad path"}), 404
        if not os.path.isfile(path):
            return jsonify({"error": "файл удалён"}), 404
        return send_file(path, mimetype="image/jpeg")

    # ---- hook: камера сама сообщает о движении (GET/POST, без CSRF) ----

    def _motion_hook(token):
        c = motion_engine.cfg()
        expected = (c.get("hook_token") or "").strip()
        if not expected or not hmac.compare_digest(str(token), str(expected)):
            return jsonify({"ok": False, "error": "bad token"}), 404
        data = request.get_json(silent=True) or {}
        cam_id = str(request.args.get("cam") or data.get("cam") or "").strip()
        if not cam_id:
            return jsonify({"ok": False, "error": "cam required"}), 400
        from app import _check_rate
        if not _check_rate(f"motion_hook_{cam_id}", 2):
            return jsonify({"ok": True, "skipped": "rate"}), 200
        cam = motion_engine._cam_by_id(cam_id)
        try:
            score = float(data.get("score") or request.args.get("score") or 1.0)
        except (TypeError, ValueError):
            score = 1.0
        eid = motion_engine.trigger_event(cam_id, score, "hook", cam=cam)
        return jsonify({"ok": True, "event": eid,
                        "cooldown": eid is None})

    hook_view = _motion_hook
    # ВАЖНО:csrf берём из app.extensions (экземпляр именно ЭТОГО приложения) —
    # import app при запуске как __main__ создаёт вторую копию модуля.
    _csrf = getattr(app, "extensions", {}).get("csrf")
    if _csrf is not None:
        hook_view = _csrf.exempt(_motion_hook)
    app.add_url_rule("/api/motion/hook/<token>", "motion_hook", hook_view,
                     methods=["GET", "POST"])

    @app.route("/api/motion/notify/test", methods=["POST"])
    @admin_required
    def motion_notify_test():
        body = request.get_json(silent=True) or {}
        channel = (body.get("channel") or request.form.get("channel")
                   or "").strip()
        if channel not in ("telegram", "email"):
            return jsonify({"ok": False,
                            "error": "channel: telegram|email"}), 400
        c = motion_engine.cfg()
        ch = (c.get("channels") or {}).get(channel) or {}
        if not ch.get("enabled"):
            return jsonify({"ok": False, "error": "канал выключен"}), 400
        from modules import motion_notify
        try:
            motion_notify.send_test(channel, ch)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        return jsonify({"ok": True})

    @app.route("/api/motion/silence", methods=["POST"])
    @admin_required
    def motion_silence():
        body = request.get_json(silent=True) or {}
        try:
            minutes = int(body.get("minutes", 30) or 0)
        except (TypeError, ValueError):
            minutes = 30
        until = motion_engine.silence(minutes)
        return jsonify({"ok": True, "silence_until": until})

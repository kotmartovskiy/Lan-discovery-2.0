import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for, jsonify

log = logging.getLogger("lan-discovery")


# Discovery engine (PHASE 6): РґРІРёР¶РѕРє РІС‹РЅРµСЃРµРЅ РІ core/discovery.py.
# Р РµСЌРєСЃРїРѕСЂС‚ вЂ” РѕР±СЂР°С‚РЅР°СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ: app.py / system_routes РёРјРїРѕСЂС‚РёСЂСѓСЋС‚
# СЌС‚Рё СЃРёРјРІРѕР»С‹ РёР· devices_routes; reconcile РЅСѓР¶РµРЅ Рё job'Рµ СЃРєР°РЅР° (2.0-3).
from core.discovery import (  # noqa: F401
    get_hostname,
    get_scan_status,
    parse_scan,
    reconcile,
    run_scan,
    scan_loop,
    start_scan_thread,
)
from core.events import notify_all


# РЎС…РµРјР° Рё РєРѕРЅРЅРµРєС‚РѕСЂ Р‘Р” вЂ” core/db.py (PHASE 2.0-10); СЂРµСЌРєСЃРїРѕСЂС‚ СЃРёРјРІРѕР»РѕРІ вЂ”
# РѕР±СЂР°С‚РЅР°СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ (СЃРїРµРєР° В§32): app/system_routes/tests РёРјРїРѕСЂС‚РёСЂСѓСЋС‚
# init_db_schema/get_db/DB РѕС‚СЃСЋРґР°.
from core.db import (  # noqa: F401
    DB,
    SCHEMA_VERSION,
    get_db,
    init_db_schema,
)

def register_routes(app, ctx):
    login_required = ctx.login_required
    can_edit = ctx.can_edit
    admin_required = ctx.admin_required
    page_data = ctx.page_data
    _cfg = ctx._cfg


    @app.route("/devices")
    @login_required
    def index():

        con = get_db()
        try:
            devices = con.execute(
                """
                SELECT
                    ip,
                    online,
                    name,
                    hostname,
                    mac,
                    vendor,
                    first_seen,
                    last_seen,
                    misses,
                    appearances,
                    is_new,
                    device_type
                FROM devices

                ORDER BY
                    is_new DESC,
                    online DESC,
                    ip
                """
            ).fetchall()

            total = len(devices)

            online = sum(
                1
                for d in devices
                if d[1]
            )
        finally:
            con.close()

        prepared = []

        for d in devices:

            prepared.append(
                (
                    d[0],
                    d[1],
                    d[2],
                    d[3],
                    d[4],
                    d[5],
                    d[6],
                    d[7],
                    d[8],
                    d[9],
                    d[10],
                    d[11]
                )
            )

        data = page_data()

        return render_template("devices.html",
            devices=prepared,
            total=total,
            online=online,
            **data
        )

    @app.route("/history")
    @login_required
    def history():

        con = get_db()
        try:
            events = con.execute(
                """
                SELECT
                    timestamp,
                    ip,
                    hostname,
                    mac,
                    event,
                    severity

                FROM events

                ORDER BY id DESC

                LIMIT 500
                """
            ).fetchall()
        finally:
            con.close()

        data = page_data()

        return render_template("history.html",
            events=events,
            **data
        )

    @app.route("/device/<ip>")
    @login_required
    def device(ip):

        con = get_db()
        try:
            device = con.execute(
                """
                SELECT
                    ip,
                    online,
                    name,
                    hostname,
                    mac,
                    vendor,
                    first_seen,
                    last_seen,
                    misses,
                    appearances,
                    is_new,
                    device_type
                FROM devices
                WHERE ip=?
                """,
                (ip,)
            ).fetchone()

            if not device:

                return "РЈСЃС‚СЂРѕР№СЃС‚РІРѕ РЅРµ РЅР°Р№РґРµРЅРѕ", 404

            events = con.execute(
                """
                SELECT
                    timestamp,
                    event,
                    severity

                FROM events

                WHERE ip=?

                ORDER BY id DESC

                LIMIT 100
                """,
                (ip,)
            ).fetchall()
        finally:
            con.close()

        try:
            from modules.inventory import get_inventory
            inventory = get_inventory(ip) or {}
        except Exception:
            inventory = {}

        data = page_data()

        return render_template("device.html",
            device=device,
            events=events,
            inventory=inventory,
            **data
        )

    @app.route("/device/<ip>/name", methods=["POST"])
    @can_edit
    @login_required
    def set_name(ip):

        name = request.form.get(
            "name",
            ""
        ).strip()

        device_type = request.form.get(
            "device_type",
            ""
        ).strip()

        con = get_db()
        try:
            con.execute(
                """
                UPDATE devices
                SET name=?, device_type=?
                WHERE ip=?
                """,
                (
                    name if name else None,
                    device_type if device_type else None,
                    ip
                )
            )

            con.commit()
        finally:
            con.close()

        return redirect(
            url_for(
                "device",
                ip=ip
            )
        )

    @app.route("/api/device/<ip>/dismiss-new", methods=["POST"])
    @can_edit
    @login_required
    def dismiss_new(ip):
        con = get_db()
        try:
            con.execute("UPDATE devices SET is_new=0 WHERE ip=?", (ip,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @app.route("/api/device/<ip>/identity")
    @login_required
    def device_identity(ip):
        """РРґРµРЅС‚РёС‡РЅРѕСЃС‚СЊ СѓСЃС‚СЂРѕР№СЃС‚РІР° (2.0-11, В§15): device_id + IP history."""
        from core import identity as identity_core

        con = get_db()
        try:
            ident = identity_core.identity_of(con, ip)
        finally:
            con.close()
        if ident is None:
            return jsonify({"ok": False,
                            "error": "СѓСЃС‚СЂРѕР№СЃС‚РІРѕ РЅРµ РЅР°Р№РґРµРЅРѕ"}), 404
        return jsonify({"ok": True, **ident})

    @app.route("/api/events")
    @login_required
    def api_events():
        """Р¤РёР»СЊС‚СЂРѕРІР°РЅРЅР°СЏ Р»РµРЅС‚Р° СЃРѕР±С‹С‚РёР№ (P7-3): ?limit=&event=&severity=&ip=."""
        from core.events import list_events, event_to_dict

        try:
            limit = max(1, min(2000, int(request.args.get("limit", 500))))
        except (TypeError, ValueError):
            limit = 500

        con = get_db()
        try:
            rows = list_events(
                con,
                limit=limit,
                event=request.args.get("event") or None,
                severity=request.args.get("severity") or None,
                ip=request.args.get("ip") or None,
                source=request.args.get("source") or None,
            )
        finally:
            con.close()

        return jsonify({"ok": True, "events": [event_to_dict(r)
                                               for r in rows]})

    @app.route("/api/scan", methods=["POST"])
    @admin_required
    def api_scan():
        """Р СѓС‡РЅРѕРµ СЃРєР°РЅРёСЂРѕРІР°РЅРёРµ (P6-2; 2.0-3 вЂ” job, one-shot, Р±РµР· settings).

        РўРµР»Рѕ (JSON, РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ): {"subnet": "192.168.1.0/24",
        "ifaces": ["eth0"]}. Р‘РµР· РїР°СЂР°РјРµС‚СЂРѕРІ вЂ” С‚РµРєСѓС‰Р°СЏ РєРѕРЅС„РёРіСѓСЂР°С†РёСЏ.
        РћС‚РІРµС‚: {"ok": true, "job": "<id>"} вЂ” СЃС‚Р°С‚СѓСЃ Рё СЂРµР·СѓР»СЊС‚Р°С‚ Р·Р°РґР°С‡Рё:
        GET /api/jobs/<id> (Р°РґРґРёС‚РёРІРЅРѕ Рє 1.1: devices/stats/subnet С‚РµРїРµСЂСЊ
        РІ job.result; РѕС€РёР±РєР° nmap вЂ” СЃС‚Р°С‚СѓСЃ job=failed).
        """
        data = request.get_json(silent=True) or {}
        subnet = (data.get("subnet") or "").strip() or None
        ifaces = data.get("ifaces")
        if ifaces is not None and (
            not isinstance(ifaces, list)
            or not ifaces
            or not all(isinstance(i, str) and i.strip() for i in ifaces)
        ):
            return jsonify(
                {"ok": False, "error": "ifaces: РЅРµРїСѓСЃС‚РѕР№ СЃРїРёСЃРѕРє СЃС‚СЂРѕРє"}
            ), 400
        if ifaces:
            ifaces = [i.strip() for i in ifaces]

        from core import jobs
        jid = jobs.submit(
            "network-scan",
            lambda ctx: _scan_job(ctx, subnet, ifaces),
            meta={"subnet": subnet or "Р°РІС‚Рѕ"},
        )
        return jsonify({"ok": True, "job": jid})


def _current_subnet():
    from core.config import get as _cfg
    return _cfg("network", "subnet", "192.168.3.0/24")


def _scan_job(ctx, subnet, ifaces):
    """JOB (2.0-3): СЂСѓС‡РЅРѕР№ СЃРєР°РЅ вЂ” nmap + reconcile РІ С„РѕРЅРµ.

    cancelable РЅРµ СЃС‚Р°РІРёРј: nmap-РїСЂРѕРіРѕРЅ РёР·РЅСѓС‚СЂРё РЅРµ РїСЂРµСЂРІР°С‚СЊ (РєР°Р¶РґС‹Р№ в‰¤45s,
    РїРѕР»РЅС‹Р№ СЃРєР°РЅ вЂ” РЅРµСЃРєРѕР»СЊРєРѕ РїСЂРѕРіРѕРЅРѕРІ), С‡РµСЃС‚РЅРѕР№ РѕС‚РјРµРЅС‹ РїРѕСЃСЂРµРґРё РЅРµС‚.
    """
    ctx.log("РЎРєР°РЅ: Р·Р°РїСѓСЃРє nmap (%s)" % (subnet or "С‚РµРєСѓС‰Р°СЏ РїРѕРґСЃРµС‚СЊ"))
    ctx.progress(10)
    out = run_scan(subnet=subnet, ifaces=ifaces)
    if out is None:
        raise RuntimeError(
            "СЃРєР°РЅРёСЂРѕРІР°РЅРёРµ РЅРµРґРѕСЃС‚СѓРїРЅРѕ (nmap РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚ "
            "РёР»Рё РІСЃРµ РїСЂРѕРіРѕРЅС‹ СѓРїР°Р»Рё)"
        )
    ctx.progress(60)
    current = parse_scan(out)
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    con = get_db()
    events_out = []
    try:
        stats = reconcile(con, current, now, events_out=events_out)
        con.commit()
    finally:
        con.close()

    # B-03: fan-out СЃРѕР±С‹С‚РёР№ (Automation В§17) вЂ” СЃС‚СЂРѕРіРѕ РїРѕСЃР»Рµ commit
    if events_out:
        notify_all(events_out)

    ctx.progress(100)
    ctx.log("РЎРєР°РЅ Р·Р°РІРµСЂС€С‘РЅ: %d СѓСЃС‚СЂРѕР№СЃС‚РІ" % len(current))
    return {
        "devices": len(current),
        "stats": stats,
        "subnet": subnet or _current_subnet(),
    }

"""Админка модулей: страница /modules, установка зависимостей, вкл/выкл.

Регистрирует:
  - context_processor: nav_items / nav_groups / help_sections / page
  - before_request:    404 для маршрутов выключенных модулей
  - GET  /modules                 — список модулей (только admin)
  - POST /modules/<mid>/install   — установка deps (apt/pip/services/dirs)
  - POST /modules/<mid>/toggle    — включение/выключение модуля
  - GET  /roles + POST /roles/<rid>/apply   — профили модулей (STEP 9)
  - GET  /api/roles + POST /api/roles/<rid>/apply — JSON-вариант (STEP 9)
"""
import os
import subprocess
import sys
import time
import urllib.parse

from flask import jsonify, redirect, render_template, request

from core import jobs
from core import manifest as manifest_mod
from core import syschange
from core.module_catalog import (
    CatalogError,
    catalog_module_ids,
    fetch_index,
    install_module,
    remove_module,
)
from core.module_loader import (
    desktop_categories,
    discover_modules,
    disabled_prefixes,
    get_module,
    help_sections,
    load_state,
    module_status,
    modules_with_status,
    nav_groups,
    nav_items,
    active_page,
    record_install_result,
    set_module_status,
)
from core.roles import apply_role, roles_overview


def _run(cmd, timeout=600):
    try:
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out[-4000:]
    except Exception as e:
        return False, str(e)


def _install_manifest(m, ctx=None):
    """Установка deps модуля. ctx (2.0-3, JobContext | None): лог шагов,
    прогресс, cooperative-отмена между шагами (сам apt/pip не прерываем).
    """
    deps = m.get("deps") or {}
    steps = []
    ok_all = True

    def _step(text):
        steps.append(text)
        if ctx:
            ctx.log(text)

    pkgs = deps.get("apt") or []
    if pkgs:
        if ctx:
            ctx.check_cancel()
            ctx.progress(15)
        # §26: preflight→backup→apply→verify (+rollback при сбое)
        try:
            syschange.run(
                [{"type": "apt_install", "packages": pkgs}],
                run_cmd=_run,
                log=_step,
            )
            _step("apt install " + " ".join(pkgs) + ": OK")
        except syschange.SystemChangeError as e:
            ok_all = False
            _step("apt install " + " ".join(pkgs) + ": FAIL")
            _step(str(e)[-1500:])

    pypkgs = deps.get("pip") or []
    if pypkgs:
        if ctx:
            ctx.check_cancel()
            ctx.progress(50)
        ok, out = _run([sys.executable, "-m", "pip", "install"] + pypkgs)
        _step("pip install " + " ".join(pypkgs) + ": " + ("OK" if ok else "FAIL"))
        if not ok:
            ok_all = False
            _step(out[-1500:])

    if deps.get("dirs"):
        if ctx:
            ctx.check_cancel()
            ctx.progress(75)
    for d in deps.get("dirs") or []:
        try:
            os.makedirs(d, exist_ok=True)
            _step("mkdir %s: OK" % d)
        except Exception as e:
            ok_all = False
            _step("mkdir %s: FAIL (%s)" % (d, e))

    if deps.get("services"):
        if ctx:
            ctx.check_cancel()
            ctx.progress(85)
    for svc in deps.get("services") or []:
        ok, out = _run(["systemctl", "enable", "--now", svc])
        _step("service %s: " % svc + ("OK" if ok else "FAIL"))
        if not ok:
            ok_all = False
            _step(out[-1500:])

    if ctx:
        ctx.progress(95)

    return {
        "ts": time.strftime("%d.%m.%Y %H:%M:%S"),
        "ok": ok_all,
        "log": steps,
    }


def _module_install_job(ctx, m, installed_before):
    """JOB (2.0-3): установка deps + фиксация состояния модуля в фоне."""
    mid = m.get("id", "?")
    ctx.log("Установка «%s»: старт" % mid)
    ctx.progress(5)
    result = _install_manifest(m, ctx=ctx)
    record_install_result(mid, result)
    if not installed_before:
        set_module_status(mid, installed=True, enabled=True)
    if not result.get("ok"):
        raise RuntimeError("не все зависимости установились (см. лог)")
    ctx.progress(100)
    ctx.log("Установка «%s»: готово" % mid)
    return {"module": mid, "ok": True}


def _catalog_install_job(ctx, mid, update):
    """JOB (2.0-3): установка/обновление из каталога (сеть + распаковка).

    CatalogError из install_module → failed со текстом ошибки в job.
    """
    action = "обновление" if update else "установка"
    ctx.log("Каталог: %s «%s»" % (action, mid))
    ctx.progress(10)
    install_module(mid, update=update)
    ctx.progress(100)
    ctx.log("Каталог: «%s» готово" % mid)
    return {"module": mid, "update": bool(update)}


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    page_data = ctx.page_data
    get_current_user = ctx.get_current_user

    @app.context_processor
    def _inject_modules_context():
        u = get_current_user()
        role = getattr(u, "role", "") if u else ""
        return {
            "nav_items": nav_items(),
            "nav_groups": nav_groups(admin=(role == "admin")),
            "help_sections": help_sections(),
            "desktop_categories": desktop_categories(),
            "page": active_page(request.path),
        }

    @app.before_request
    def _gate_disabled_modules():
        if request.method != "GET":
            return None
        path = request.path
        if path.startswith("/static/") or path == "/login":
            return None
        for pfx in disabled_prefixes():
            if path == pfx or path.startswith(pfx + "/"):
                return ("Модуль отключен", 404)
        return None

    @app.route("/modules")
    @admin_required
    def modules_page():
        mods = modules_with_status()

        catalog, catalog_error = None, None
        try:
            catalog = fetch_index()
        except CatalogError as e:
            catalog_error = str(e)

        return render_template(
            "modules.html",
            mods=mods,
            catalog=catalog,
            catalog_error=catalog_error,
            catalog_ids=catalog_module_ids(),
            local_ids={m["id"] for m in discover_modules()},
            cat_ok=request.args.get("ok") or "",
            cat_err=request.args.get("err") or "",
            perm_text=manifest_mod.install_confirm_text,
            perm_info=manifest_mod.permission_info,
            **page_data(),
        )

    @app.route("/modules/catalog/refresh", methods=["POST"])
    @admin_required
    def modules_catalog_refresh():
        try:
            fetch_index(refresh=True)
            return redirect("/modules?ok=" + urllib.parse.quote("Каталог обновлён"))
        except CatalogError as e:
            return redirect("/modules?err=" + urllib.parse.quote(str(e)))

    @app.route("/modules/<mid>/catalog/install", methods=["POST"])
    @admin_required
    def modules_catalog_install(mid):
        jid = jobs.submit(
            "module-catalog-install",
            lambda ctx: _catalog_install_job(ctx, mid, False),
            meta={"module": mid},
        )
        return redirect("/modules?ok=" + urllib.parse.quote(
            "Установка «%s» из каталога запущена (задача %s)"
            % (mid, jid)))

    @app.route("/modules/<mid>/catalog/update", methods=["POST"])
    @admin_required
    def modules_catalog_update(mid):
        jid = jobs.submit(
            "module-catalog-update",
            lambda ctx: _catalog_install_job(ctx, mid, True),
            meta={"module": mid},
        )
        return redirect("/modules?ok=" + urllib.parse.quote(
            "Обновление «%s» из каталога запущено (задача %s)"
            % (mid, jid)))

    @app.route("/modules/<mid>/catalog/remove", methods=["POST"])
    @admin_required
    def modules_catalog_remove(mid):
        try:
            remove_module(mid)
            return redirect("/modules?ok=" + urllib.parse.quote("Модуль «%s» удалён" % mid))
        except CatalogError as e:
            return redirect("/modules?err=" + urllib.parse.quote(str(e)))

    @app.route("/modules/<mid>/toggle", methods=["POST"])
    @admin_required
    def modules_toggle(mid):
        m = get_module(mid)
        if not m:
            return ("Модуль не найден", 404)
        installed, enabled = module_status(mid)
        if installed:
            set_module_status(mid, enabled=not enabled)
        return redirect("/modules")

    @app.route("/modules/<mid>/install", methods=["POST"])
    @admin_required
    def modules_install(mid):
        m = get_module(mid)
        if not m:
            return ("Модуль не найден", 404)
        installed_before, _ = module_status(mid)
        jid = jobs.submit(
            "module-install",
            lambda ctx: _module_install_job(ctx, m, installed_before),
            cancelable=True,
            meta={"module": mid},
        )
        return redirect("/modules?ok=" + urllib.parse.quote(
            "Установка «%s» запущена (задача %s)" % (mid, jid)))

    # --- Roles layer (STEP 9): конфиг-профили модулей + compat-check ---

    @app.route("/roles")
    @admin_required
    def roles_page():
        return render_template(
            "roles.html",
            ov=roles_overview(),
            ok_msg=request.args.get("ok") or "",
            err_msg=request.args.get("err") or "",
            **page_data(),
        )

    @app.route("/roles/<rid>/apply", methods=["POST"])
    @admin_required
    def roles_apply(rid):
        res = apply_role(rid)
        if res.get("ok"):
            msg = ("Роль «%s» применена: включено %d, выключено %d, "
                   "пропущено %d" % (rid, len(res["enabled"]),
                                     len(res["disabled"]), len(res["skipped"])))
            return redirect("/roles?ok=" + urllib.parse.quote(msg))
        return redirect("/roles?err=" + urllib.parse.quote(
            res.get("error") or "Не удалось применить роль"))

    @app.route("/api/roles")
    @login_required
    def api_roles():
        return jsonify(roles_overview())

    @app.route("/api/roles/<rid>/apply", methods=["POST"])
    @admin_required
    def api_roles_apply(rid):
        return jsonify(apply_role(rid))

# -*- coding: utf-8 -*-
"""Disk Manager — API расширенной утилиты «Диски» (модуль disks).

Все мутации — admin + CSRF; долгие операции (клон, запись образа,
скачивание FreeDOS) идут в фоне с поллингом /api/disktool/status.
"""
import os
import threading
import time

from flask import jsonify, request

from core import disktool
from core import storage as core_storage
from core.hardware import hdd_device

_rate = {}


def register_routes(app, ctx):
    login_required = ctx.login_required
    admin_required = ctx.admin_required
    page_data = ctx.page_data

    def _check_rate(key, sec):
        now = time.time()
        if now - _rate.get(key, 0) < sec:
            return False
        _rate[key] = now
        return True

    def _deny(ok, reason):
        if not ok:
            return jsonify({"ok": False, "error": reason}), 400
        return None

    # --- листинг / разделы / SMART ----------------------------------------

    @app.route("/api/disktool/disks")
    @login_required
    def disktool_disks():
        try:
            disks = disktool.lsblk_disks()
            smart_dev = hdd_device()
            for d in disks:
                d["smart_default"] = (d["name"] == smart_dev)
            return jsonify({"ok": True, "disks": disks})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/partitions")
    @login_required
    def disktool_partitions():
        dev = request.args.get("dev", "")
        try:
            return jsonify({"ok": True, **disktool.parted_print(dev)})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/smart")
    @login_required
    def disktool_smart():
        dev = request.args.get("dev", "")
        try:
            return jsonify({"ok": True, **disktool.smart_report(dev)})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- деструктивные операции (admin) ------------------------------------

    @app.route("/api/disktool/wipe", methods=["POST"])
    @admin_required
    @login_required
    def disktool_wipe():
        if not _check_rate("wipe", 30):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        dev = data.get("dev", "")
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_whole_disk(disks, dev)
        deny = _deny(ok, reason)
        if deny:
            return deny
        try:
            return jsonify(disktool.wipe_disk(dev))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/part", methods=["POST"])
    @admin_required
    @login_required
    def disktool_part():
        if not _check_rate("part", 15):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        dev = data.get("dev", "")
        fs = data.get("fs") or ""
        try:
            start = int(data.get("start"))
            end = int(data.get("end"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "start/end — целые байты"}), 400
        if end <= start:
            return jsonify({"ok": False, "error": "end <= start"}), 400
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_whole_disk(disks, dev)
        deny = _deny(ok, reason)
        if deny:
            return deny
        try:
            return jsonify(disktool.add_partition(dev, fs, start, end))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/part-del", methods=["POST"])
    @admin_required
    @login_required
    def disktool_part_del():
        if not _check_rate("partdel", 10):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        dev = data.get("dev", "")
        num = data.get("num")
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_whole_disk(disks, dev)
        deny = _deny(ok, reason)
        if deny:
            return deny
        try:
            return jsonify(disktool.del_partition(dev, int(num)))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/format", methods=["POST"])
    @admin_required
    @login_required
    def disktool_format():
        if not _check_rate("format", 30):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        part = data.get("dev", "")
        fs = data.get("fs", "")
        label = (data.get("label") or "").strip()
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_partition(disks, part)
        deny = _deny(ok, reason)
        if deny:
            return deny
        try:
            return jsonify(disktool.format_partition(part, fs, label))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/disktool/fsck", methods=["POST"])
    @admin_required
    @login_required
    def disktool_fsck():
        data = request.get_json(silent=True) or {}
        part = data.get("dev", "")
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_partition(disks, part)
        # fsck -n read-only: разрешаем на смонтированных? нет — риск ложных
        # срабатываний на работающих ФС; требуем несмонтированный раздел
        deny = _deny(ok, reason)
        if deny:
            return deny
        try:
            return jsonify(disktool.fsck_partition(part))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- долгие операции: клон / запись образа ------------------------------

    def _start_op(name, target, fn):
        st = disktool.load_state()
        if st.get("running"):
            return jsonify({"ok": False,
                            "error": "другая операция уже идёт"}), 409
        disktool.update_state(running=True, ok=False, error=None, percent=0,
                              op=name, target=target, text="подготовка...",
                              started=time.time())

        def worker():
            try:
                res = fn()
                disktool.update_state(running=False, ok=bool(res.get("ok")),
                                      error=res.get("error"),
                                      percent=100 if res.get("ok") else None,
                                      text="готово" if res.get("ok")
                                      else "ошибка")
            except Exception as e:
                disktool.update_state(running=False, error=str(e))

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"ok": True})

    @app.route("/api/disktool/clone", methods=["POST"])
    @admin_required
    @login_required
    def disktool_clone():
        if not _check_rate("clone", 60):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        src = data.get("src", "")
        dst = data.get("dst", "")
        if not src or not dst or src == dst:
            return jsonify({"ok": False,
                            "error": "выберите два разных диска"}), 400
        disks = disktool.lsblk_disks()
        ok_s, reason_s = disktool.check_whole_disk(disks, src)
        deny = _deny(ok_s, "источник: " + reason_s)
        if deny:
            return deny
        ok_d, reason_d = disktool.check_whole_disk(disks, dst)
        deny = _deny(ok_d, "приёмник: " + reason_d)
        if deny:
            return deny
        src_disk = disktool.find_disk(disks, src)
        dst_disk = disktool.find_disk(disks, dst)
        if src_disk["size"] > dst_disk["size"]:
            return jsonify({"ok": False,
                            "error": "приёмник меньше источника"}), 400

        def run():
            return core_storage.clone_disk(
                "/dev/" + src, "/dev/" + dst,
                on_progress=lambda p, t: disktool.update_state(percent=p,
                                                               text=t),
                should_continue=lambda: bool(disktool.load_state()
                                             .get("running")),
            )
        return _start_op("clone", src + " → " + dst, run)

    @app.route("/api/disktool/write", methods=["POST"])
    @admin_required
    @login_required
    def disktool_write():
        if not _check_rate("write", 60):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        key = data.get("image", "")
        dev = data.get("dev", "")
        path = disktool.resolve_image(key)
        if not path:
            return jsonify({"ok": False, "error": "образ не найден"}), 400
        disks = disktool.lsblk_disks()
        ok, reason = disktool.check_whole_disk(disks, dev)
        deny = _deny(ok, reason)
        if deny:
            return deny
        img_size = os.path.getsize(path)
        dst_size = disktool.find_disk(disks, dev)["size"]
        if img_size > dst_size:
            return jsonify({"ok": False,
                            "error": "образ больше диска"}), 400

        def run():
            return disktool.write_image(
                path, dev,
                on_progress=lambda p, t: disktool.update_state(percent=p,
                                                               text=t),
                should_continue=lambda: bool(disktool.load_state()
                                             .get("running")),
            )
        return _start_op("write", key + " → /dev/" + dev, run)

    @app.route("/api/disktool/op/stop", methods=["POST"])
    @admin_required
    @login_required
    def disktool_op_stop():
        st = disktool.load_state()
        if st.get("running"):
            disktool.update_state(running=False,
                                  text="остановлено пользователем")
        return jsonify({"ok": True})

    @app.route("/api/disktool/status")
    @login_required
    def disktool_status():
        return jsonify(disktool.load_state())

    # --- образы (FreeDOS + загрузка) ---------------------------------------

    @app.route("/api/disktool/images")
    @login_required
    def disktool_images():
        return jsonify({"ok": True, "images": disktool.list_images()})

    @app.route("/api/disktool/images/freedos", methods=["POST"])
    @admin_required
    @login_required
    def disktool_freedos():
        if not _check_rate("freedos", 300):
            return jsonify({"error": "too fast"}), 429
        data = request.get_json(silent=True) or {}
        key = data.get("key", "")
        st = disktool.load_state()
        if st.get("running"):
            return jsonify({"ok": False,
                            "error": "другая операция уже идёт"}), 409
        disktool.update_state(running=True, ok=False, op="freedos",
                              target=key, percent=0, text="скачивание...",
                              started=time.time())

        def worker():
            try:
                res = disktool.download_freedos(
                    key,
                    on_progress=lambda p, t: disktool.update_state(
                        percent=p, text=t),
                )
                disktool.update_state(running=False, ok=bool(res.get("ok")),
                                      error=res.get("error"), percent=100,
                                      text="готово" if res.get("ok")
                                      else "ошибка")
            except Exception as e:
                disktool.update_state(running=False, error=str(e))

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"ok": True})

    @app.route("/api/disktool/images/upload", methods=["POST"])
    @admin_required
    @login_required
    def disktool_upload():
        f = request.files.get("file")
        if f is None or not f.filename:
            return jsonify({"ok": False, "error": "файл не передан"}), 400
        try:
            return jsonify(disktool.save_upload(f.filename, f.stream))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

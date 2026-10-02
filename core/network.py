# -*- coding: utf-8 -*-
"""Network layer — Core Network Manager (спека §5–6, PHASE 2.0-6).

Два слоя:

1. Read-only объектный API — ``list_interfaces``/``list_addresses``/
   ``list_routes``/``wifi_scan``: модули читают сеть через Core, а не
   напрямую subprocess (спека §5). Потребители: роут ``/api/network/info``
   (sys-network block), ``/api/wifi/scan`` (перенос парсера iw).

2. Транзакционные мутации (спека §6)::

    Prepare → Apply → Verify → Commit, при ошибке → Rollback

   ``set_address``/``del_address``/``set_route``/``del_route`` — первый
   перенос (ROADMAP 2.0-6: сетевые операции уходят из модулей в Core).
   DHCP/DNS/VPN/AP/bridge — по мере модулей-потребителей, firewall —
   отдельной фазой (nft-модель ещё не спроектирована).

Особенности:
- валидация входа ДО транзакции (имя интерфейса ≤15 символов, CIDR,
  dst/gateway — строгие форматы; shell-инъекции нет — argv, не shell);
- снимок состояния (``prepare``) — это и есть rollback-данные;
- ``verify`` — немедленная проверка результата команды (спека §6:
  «не должно просто выполнять несколько shell-команд без проверки»);
- insurance — отложенная страховка: через ``grace`` секунд после commit
  ``check_fn()`` → при провале авто-rollback применённых шагов в
  daemon-треде (панель переживает потерю связи — цель: вернуть доступ,
  если новый конфиг отвязал сессию);
- API никогда не бросает исключений наружу — все провалы это dict
  ``{ok: False, error, phase, ...}`` (стиль core.*).

Потребители (PHASE 2.0-2, сохранены): ``core.capabilities._net_ifaces``
→ ``physical_ifaces`` (контракт sysfs-парсинга не менялся).
"""
import ipaddress
import json
import logging
import os
import re
import threading
import time

from core import process

log = logging.getLogger(__name__)


# --- read-only: sysfs ------------------------------------------------------

def physical_ifaces(base="/sys/class/net"):
    """(wired, wireless) — списки имён физических интерфейсов из sysfs.

    Виртуальные (lo, veth, docker0, bridge — нет device/ и wireless/)
    пропускаются. None — источник недоступен (не-Linux / ошибка чтения):
    вызывающий обязан ответить unknown/unverified, а не absent.
    """
    if not os.path.isdir(base):
        return None
    try:
        names = os.listdir(base)
    except Exception:
        return None
    wired, wireless = [], []
    for n in sorted(names):
        if n == "lo":
            continue
        p = base + "/" + n
        is_wifi = os.path.isdir(p + "/wireless")
        if not is_wifi and not os.path.exists(p + "/device"):
            continue
        (wireless if is_wifi else wired).append(n)
    return wired, wireless


# --- read-only: iproute2 JSON ---------------------------------------------

def _ip(args, timeout=10):
    """``ip <args>`` — CompletedProcess | None (бинарь/таймаут/прочее)."""
    try:
        return process.run(["ip"] + list(args), timeout=timeout)
    except Exception:
        return None


def _ip_json(args, timeout=10):
    """``ip -j <args>`` → parsed JSON (list/dict) | None (не-Linux/ошибка)."""
    r = _ip(["-j"] + list(args), timeout=timeout)
    if r is None or r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "[]")
    except Exception:
        return None


def _ip_rc(args, timeout=10):
    """``ip <args>`` → True при rc==0 (apply-шаги мутаций)."""
    r = _ip(list(args), timeout=timeout)
    return r is not None and r.returncode == 0


def list_interfaces(base="/sys/class/net"):
    """[{name, type, state, up, mac, mtu}] | None (iproute2 недоступен).

    type: wired/wifi — из sysfs-классификации physical_ifaces (Unknown
    при недоступном sysfs), loopback — lo, virtual — остальное.
    """
    data = _ip_json(["link"])
    if data is None:
        return None
    phys = physical_ifaces(base)
    if phys is None:
        wired, wireless = set(), set()
        unknown = True
    else:
        wired, wireless = set(phys[0]), set(phys[1])
        unknown = False
    out = []
    for it in data:
        name = it.get("ifname") or ""
        if not name:
            continue
        if name == "lo":
            typ = "loopback"
        elif name in wired:
            typ = "wired"
        elif name in wireless:
            typ = "wifi"
        elif unknown:
            typ = "unknown"
        else:
            typ = "virtual"
        state = str(it.get("operstate") or "").lower()
        out.append({
            "name": name,
            "type": typ,
            "state": state,
            "up": state == "up",
            "mac": it.get("address") or "",
            "mtu": it.get("mtu"),
        })
    return out


def list_addresses():
    """[{iface, family, addr, prefixlen, scope, dynamic}] | None."""
    data = _ip_json(["addr"])
    if data is None:
        return None
    out = []
    for it in data:
        iface = it.get("ifname") or ""
        for a in it.get("addr_info") or []:
            out.append({
                "iface": iface,
                "family": a.get("family"),
                "addr": a.get("local") or "",
                "prefixlen": a.get("prefixlen"),
                "scope": a.get("scope") or "",
                "dynamic": bool(a.get("dynamic")),
            })
    return out


def list_routes():
    """[{dst, gateway, dev, metric, protocol, table}] | None (main table)."""
    data = _ip_json(["route"])
    if data is None:
        return None
    out = []
    for it in data:
        out.append({
            "dst": it.get("dst") or "",
            "gateway": it.get("gateway") or "",
            "dev": it.get("dev") or "",
            "metric": it.get("metric"),
            "protocol": it.get("protocol") or "",
            "table": it.get("table") or "",
        })
    return out


# --- read-only: Wi-Fi scan (перенос из network_routes, PHASE 2.0-6) -------

def _parse_iw_scan(output):
    """Парсер вывода ``iw dev <iface> scan`` → list сетей (контракт 1.1)."""
    networks = []
    current = {}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("BSS "):
            if current and current.get("ssid"):
                networks.append(current)
            bssid = line.split("(")[0].replace("BSS ", "")
            current = {"bssid": bssid, "ssid": "", "channel": 0, "frequency": 0,
                       "signal": -100, "signal_pct": 0, "encryption": "",
                       "bandwidth": "20MHz"}
        elif line.startswith("SSID: "):
            current["ssid"] = line[6:]
        elif line.startswith("freq: "):
            try:
                current["frequency"] = int(line[6:])
                freq = current["frequency"]
                if freq < 3000:
                    if freq == 2484:
                        current["channel"] = 14
                    else:
                        current["channel"] = (freq - 2412) // 5 + 1
                else:
                    current["channel"] = (freq - 5000) // 5
            except ValueError:
                pass
        elif line.startswith("signal: "):
            try:
                sig_str = line[8:].split(" ")[0]
                current["signal"] = float(sig_str)
                current["signal_pct"] = max(0, min(100, int((float(sig_str) + 100) * 2)))
            except ValueError:
                pass
        elif "RSN:" in line or "WPA:" in line:
            current["encryption"] = "WPA2" if "RSN:" in line else "WPA"
        elif line.startswith("secondary channel"):
            if "above" in line or "below" in line:
                current["bandwidth"] = "40MHz"
        elif "VHT" in line or "HE" in line:
            current["bandwidth"] = "80MHz+"
    if current and current.get("ssid"):
        networks.append(current)
    networks.sort(key=lambda x: x["signal"], reverse=True)
    return networks


def wifi_scan(ifaces=None, timeout=15):
    """Скан Wi-Fi через ``iw`` → {ok, networks} | {ok: False, error}.

    Контракт JSON не изменился (перенос реализации из
    modules/network_routes.api_wifi_scan, PHASE 2.0-6).
    """
    if not ifaces:
        ifaces = ["wlan1", "wlan0"]
    ran = False
    last_err = None
    for iface in ifaces:
        try:
            r = process.run(["iw", "dev", iface, "scan"], timeout=timeout)
        except Exception as e:
            last_err = str(e)
            continue
        ran = True
        if r.returncode == 0:
            return {"ok": True, "networks": _parse_iw_scan(r.stdout or "")}
        last_err = (r.stderr or "").strip()[:300]
    if ran:
        # как в 1.1: iw отработал, но ни одна попытка не дала результата
        return {"ok": True, "networks": []}
    return {"ok": False, "error": last_err or "iw scan failed"}


# --- валидация входа (до транзакции) ---------------------------------------

_IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,14}$")


def _valid_iface(name):
    """Имя интерфейса: Linux IFNAMSIZ (≤15), без пробелов/'/'/leading '-'."""
    return isinstance(name, str) and bool(_IFACE_RE.match(name))


def _parse_cidr(cidr):
    """'a.b.c.d/nn' (или v6) → ip_interface | None — строгий формат."""
    if not isinstance(cidr, str):
        return None
    cidr = cidr.strip()
    if not cidr or "/" not in cidr or cidr.count("/") != 1:
        return None
    try:
        return ipaddress.ip_interface(cidr)
    except Exception:
        return None


def _parse_dst(dst):
    """'default' | 'x.x.x.0/nn' → нормализованная строка | None."""
    if not isinstance(dst, str):
        return None
    dst = dst.strip()
    if not dst or " " in dst:
        return None
    if dst == "default":
        return "default"
    try:
        return str(ipaddress.ip_network(dst, strict=False))
    except Exception:
        return None


def _err(msg):
    """Ошибка валидации/источника — до начала транзакции (phase None)."""
    return {"ok": False, "error": msg, "phase": None, "rolled_back": False}


# --- состояние интерфейса (helpers для verify/insurance) -------------------

def _addr_snapshot(iface):
    """Снимок addr_info интерфейса: list | None (iproute2 недоступен)."""
    data = _ip_json(["addr", "show", "dev", iface])
    if data is None:
        return None
    out = []
    for it in data:
        for a in it.get("addr_info") or []:
            out.append({
                "family": a.get("family"),
                "local": a.get("local"),
                "prefixlen": a.get("prefixlen"),
                "scope": a.get("scope"),
            })
    return out


def _addr_present(iface, ip, prefixlen):
    """Есть ли адрес ip/prefixlen на интерфейсе (verify). False при недоступном
    источнике — verify обязан отвечать, а не гадать (fail → rollback)."""
    data = _ip_json(["addr", "show", "dev", iface])
    if data is None:
        return False
    fam = "inet" if ip.version == 4 else "inet6"
    want = str(ip)
    for it in data:
        for a in it.get("addr_info") or []:
            if (a.get("family") == fam and a.get("local") == want
                    and a.get("prefixlen") == prefixlen):
                return True
    return False


def _restore_addrs(iface, snap):
    """Rollback: flush интерфейса и восстановление снимка (best-effort).

    Динамические (DHCP) адреса восстанавливаются как статические — это
    осознанный компромисс rollback (важнее вернуть доступность сессии).
    """
    if not snap:
        return True
    if not _ip_rc(["addr", "flush", "dev", iface], timeout=8):
        return False
    ok = True
    for a in snap:
        local, plen = a.get("local"), a.get("prefixlen")
        if not local or plen is None:
            continue
        cmd = ["addr", "replace", "%s/%s" % (local, plen), "dev", iface]
        scope = a.get("scope")
        if scope and scope != "global":
            cmd += ["scope", scope]
        if not _ip_rc(cmd, timeout=8):
            ok = False
    return ok


def _iface_operstate(iface):
    """operstate из sysfs | None (источник недоступен — не гадаем)."""
    try:
        with open("/sys/class/net/%s/operstate" % iface, "r") as f:
            return f.read().strip().lower()
    except Exception:
        return None


def _addr_insurance(iface, ip, prefixlen):
    """Отложенная проверка: адрес на месте, link не DOWN.

    Недоступный sysfs (operstate None) не считаем провалом — сам факт
    присутствия адреса уже проверен; не гадаем за источник.
    """
    if not _addr_present(iface, ip, prefixlen):
        return False
    st = _iface_operstate(iface)
    return not (st == "down")


# --- транзакционный каркас (спека §6) --------------------------------------

class Transaction:
    """Prepare → Apply → Verify → Commit; при ошибке — Rollback.

    Шаг добавляется через ``add(label, apply, prepare=None, verify=None,
    rollback=None)``; конвенция:

    - prepare/apply/verify: True/None — успех, False — провал,
      исключение — провал (текст — в error);
    - ``prepare()`` возвращает rollback-данные (любой объект; False —
      зарезервирован как сигнал провала); без prepare данные = None;
    - ``rollback(data)`` best-effort: False/исключение — откат считается
      неполным (``rolled_back: False`` в результате);
    - метки шагов должны быть уникальны (результаты по ним).

    ``run()`` не бросает исключений. Результат::

        {ok, name, phase, error, rolled_back, steps: [{label, state, error}], log}

    phase: prepare | apply | verify | commit (None у ошибок валидации —
    они не доходят до run()). state шага: pending → prepared → applied →
    verified | aborted | failed | rolled_back | no-rollback.
    """

    def __init__(self, name):
        self.name = str(name)
        self._steps = []
        self._results = []
        self._applied = []          # [(index, data)] — после успешного run
        self._log = []
        self._insurance_thread = None
        self.insurance = None       # результат отложенной страховки | None
        self.insurance_done = threading.Event()

    def add(self, label, apply, prepare=None, verify=None, rollback=None):
        if not callable(apply):
            raise ValueError("apply must be callable")
        self._steps.append({
            "label": str(label),
            "prepare": prepare,
            "apply": apply,
            "verify": verify,
            "rollback": rollback,
        })
        return self

    def _note(self, msg):
        self._log.append(msg)
        log.info("network tx[%s]: %s", self.name, msg)

    def _fail(self, phase, error, rolled):
        return {
            "ok": False,
            "name": self.name,
            "phase": phase,
            "error": error,
            "rolled_back": bool(rolled),
            "steps": [dict(s) for s in self._results],
            "log": list(self._log),
        }

    def _commit(self):
        return {
            "ok": True,
            "name": self.name,
            "phase": "commit",
            "error": None,
            "rolled_back": False,
            "steps": [dict(s) for s in self._results],
            "log": list(self._log),
        }

    def _rollback(self):
        """Откат применённых шагов в обратном порядке. True — весь успешен."""
        ok = True
        for idx, data in reversed(self._applied):
            step = self._steps[idx]
            res = self._results[idx]
            if step["rollback"] is None:
                ok = False
                res["state"] = "no-rollback"
                self._note("rollback: шаг «%s» без rollback-функции" % step["label"])
                continue
            rb_err = None
            try:
                r = step["rollback"](data)
            except Exception as e:
                rb_err = str(e)
            else:
                if r is False:
                    rb_err = "failed"
            if rb_err:
                ok = False
                # не затираем первичную ошибку apply/verify шага
                res["error"] = ("%s | rollback: %s" % (res["error"], rb_err)
                                if res.get("error") else "rollback: %s" % rb_err)
                self._note("rollback FAIL %s: %s" % (step["label"], rb_err))
                continue
            res["state"] = "rolled_back"
            self._note("rollback ok: %s" % step["label"])
        self._applied = []
        return ok

    def run(self):
        """Выполнить транзакцию целиком. См. docstring — формат результата."""
        self._applied = []
        self._log = []
        self.insurance = None
        self.insurance_done.clear()
        self._results = [
            {"label": s["label"], "state": "pending", "error": None}
            for s in self._steps
        ]
        if not self._steps:
            return self._fail("prepare", "пустая транзакция", False)

        # --- Prepare: снимки/проверки ДО любых изменений ---
        datas = []
        for i, s in enumerate(self._steps):
            data = None
            if s["prepare"] is not None:
                try:
                    data = s["prepare"]()
                except Exception as e:
                    self._results[i].update(state="aborted", error=str(e))
                    self._note("prepare FAIL %s: %s" % (s["label"], e))
                    return self._fail(
                        "prepare", "prepare шага «%s»: %s" % (s["label"], e), False)
                if data is False:
                    self._results[i].update(state="aborted", error="prepare failed")
                    self._note("prepare FAIL %s" % s["label"])
                    return self._fail(
                        "prepare", "prepare шага «%s»: prepare failed" % s["label"],
                        False)
            datas.append(data)
            self._results[i]["state"] = "prepared"
            self._note("prepare ok: %s" % s["label"])

        # --- Apply + Verify: пошагово; провал → Rollback ---
        for i, s in enumerate(self._steps):
            # rollback-данные уже есть → в откат попадает и этот шаг
            # (apply мог выполниться частично)
            self._applied.append((i, datas[i]))
            try:
                r = s["apply"]()
                err = None
            except Exception as e:
                r, err = False, str(e)
            if r is False:
                self._results[i].update(state="failed", error=err or "apply failed")
                self._note("apply FAIL %s: %s" % (s["label"], err or "failed"))
                rolled = self._rollback()
                return self._fail(
                    "apply", "apply шага «%s»: %s" % (s["label"], err or "failed"),
                    rolled)
            self._results[i]["state"] = "applied"
            self._note("apply ok: %s" % s["label"])
            if s["verify"] is not None:
                try:
                    v = s["verify"]()
                    verr = None
                except Exception as e:
                    v, verr = False, str(e)
                if v is False:
                    self._results[i].update(
                        state="failed", error=verr or "verify failed")
                    self._note("verify FAIL %s: %s" % (s["label"], verr or "failed"))
                    rolled = self._rollback()
                    return self._fail(
                        "verify",
                        "verify шага «%s»: %s" % (s["label"], verr or "failed"),
                        rolled)
                self._results[i]["state"] = "verified"
                self._note("verify ok: %s" % s["label"])

        self._note("commit: %d шаг(ов)" % len(self._steps))
        return self._commit()

    def schedule_rollback(self, check_fn, grace=20):
        """Отложенная страховка (спека §6 «авто-rollback»).

        Через ``grace`` секунд после успешного ``run()`` вызвать
        ``check_fn()``; False/исключение → авто-rollback применённых шагов
        в daemon-треде (результат — в ``self.insurance``, сигнал —
        ``self.insurance_done``). Возвращает False, если страховка не
        запускалась (нет применённых шагов, grace ≤ 0, check_fn не callable).
        """
        if not self._applied or grace <= 0 or not callable(check_fn):
            return False

        def _watch():
            time.sleep(grace)
            try:
                alive = bool(check_fn())
                err = None
            except Exception as e:
                alive, err = False, str(e)
            entry = {"checked": time.time(), "ok": alive,
                     "rolled_back": False, "error": err}
            if not alive:
                rolled = self._rollback()
                entry["rolled_back"] = True
                entry["rollback_ok"] = rolled
                self._note("insurance: check FAIL → авто-rollback (ok=%s)" % rolled)
            else:
                self._note("insurance: check ok")
            self.insurance = entry
            self.insurance_done.set()

        self._insurance_thread = threading.Thread(
            target=_watch, daemon=True, name="net-insurance")
        self._insurance_thread.start()
        return True


# --- операторы (первый перенос, ROADMAP 2.0-6) -----------------------------

def set_address(iface, cidr, insurance_grace=0):
    """Установить адрес интерфейса (ip addr replace) транзакционно.

    ``insurance_grace`` (сек) > 0 — после commit отложенная страховка:
    адрес обязан остаться на месте и link не DOWN, иначе авто-rollback
    прежнего состояния снимка.
    """
    if not _valid_iface(iface):
        return _err("недопустимое имя интерфейса")
    iface = iface.strip()
    itf = _parse_cidr(cidr)
    if itf is None:
        return _err("недопустимый CIDR (ожидается a.b.c.d/nn)")
    cidr_s = str(itf)
    snap = _addr_snapshot(iface)
    if snap is None:
        return _err("не удалось прочитать состояние интерфейса %s" % iface)
    tx = Transaction("set-address")
    tx.add(
        "адрес %s dev %s" % (cidr_s, iface),
        apply=lambda: _ip_rc(["addr", "replace", cidr_s, "dev", iface]),
        prepare=lambda: snap,
        verify=lambda: _addr_present(iface, itf.ip, itf.network.prefixlen),
        rollback=lambda data: _restore_addrs(iface, data),
    )
    res = tx.run()
    if res["ok"]:
        tx.schedule_rollback(
            lambda: _addr_insurance(iface, itf.ip, itf.network.prefixlen),
            grace=insurance_grace)
    return res


def del_address(iface, cidr, insurance_grace=0):
    """Удалить адрес интерфейса (ip addr del) транзакционно."""
    if not _valid_iface(iface):
        return _err("недопустимое имя интерфейса")
    iface = iface.strip()
    itf = _parse_cidr(cidr)
    if itf is None:
        return _err("недопустимый CIDR (ожидается a.b.c.d/nn)")
    cidr_s = str(itf)
    snap = _addr_snapshot(iface)
    if snap is None:
        return _err("не удалось прочитать состояние интерфейса %s" % iface)
    tx = Transaction("del-address")
    tx.add(
        "удалить адрес %s dev %s" % (cidr_s, iface),
        apply=lambda: _ip_rc(["addr", "del", cidr_s, "dev", iface]),
        prepare=lambda: snap,
        verify=lambda: not _addr_present(iface, itf.ip, itf.network.prefixlen),
        rollback=lambda data: _restore_addrs(iface, data),
    )
    res = tx.run()
    if res["ok"]:
        tx.schedule_rollback(
            lambda: _addr_insurance(iface, itf.ip, itf.network.prefixlen),
            grace=insurance_grace)
    return res


def _routes_json(dst):
    """``ip -j route show <dst>`` → list | None."""
    data = _ip_json(["route", "show", dst])
    return data


def _restore_routes(dst, snap):
    """Rollback маршрутов dst: flush + восстановление снимка (main table)."""
    if snap is None:
        return False
    if not snap:
        return True
    if not _ip_rc(["route", "flush", dst], timeout=8):
        return False
    ok = True
    for r in snap:
        d = r.get("dst")
        if not d:
            continue
        cmd = ["route", "replace", d]
        if r.get("gateway"):
            cmd += ["via", r["gateway"]]
        if r.get("dev"):
            cmd += ["dev", r["dev"]]
        if r.get("metric") is not None:
            cmd += ["metric", str(r["metric"])]
        if not _ip_rc(cmd, timeout=8):
            ok = False
    return ok


def set_route(dst, gateway=None, dev=None, metric=None, insurance_grace=0):
    """Заменить маршрут (ip route replace) транзакционно.

    dst — 'default' | 'x.x.x.0/nn'; хотя бы один из gateway/dev обязателен.
    """
    dst_s = _parse_dst(dst)
    if dst_s is None:
        return _err("недопустимый dst маршрута")
    if gateway is not None:
        try:
            gateway = str(ipaddress.ip_address(str(gateway).strip()))
        except Exception:
            return _err("недопустимый gateway")
    if dev is not None:
        if not _valid_iface(dev):
            return _err("недопустимое имя интерфейса")
        dev = dev.strip()
    if not gateway and not dev:
        return _err("нужен gateway или dev")
    if metric is not None:
        try:
            metric = int(metric)
            if metric < 0:
                raise ValueError
        except Exception:
            return _err("недопустимый metric")
    snap = _routes_json(dst_s)
    if snap is None:
        return _err("не удалось прочитать маршруты")
    tx = Transaction("set-route")
    cmd = ["route", "replace", dst_s]
    if gateway:
        cmd += ["via", gateway]
    if dev:
        cmd += ["dev", dev]
    if metric is not None:
        cmd += ["metric", str(metric)]

    def _verify():
        cur = _routes_json(dst_s)
        if not cur:
            return False
        for r in cur:
            if gateway and r.get("gateway") != gateway:
                continue
            if not gateway and dev and r.get("dev") != dev:
                continue
            return True
        return False

    tx.add(
        "маршрут %s%s" % (dst_s, " via %s" % gateway if gateway else ""),
        apply=lambda: _ip_rc(cmd),
        prepare=lambda: snap,
        verify=_verify,
        rollback=lambda data: _restore_routes(dst_s, data),
    )
    res = tx.run()
    if res["ok"] and insurance_grace > 0:
        tx.schedule_rollback(
            lambda: _verify(), grace=insurance_grace)
    return res


def del_route(dst, insurance_grace=0):
    """Удалить маршрут (ip route del) транзакционно."""
    dst_s = _parse_dst(dst)
    if dst_s is None:
        return _err("недопустимый dst маршрута")
    snap = _routes_json(dst_s)
    if snap is None:
        return _err("не удалось прочитать маршруты")
    tx = Transaction("del-route")
    tx.add(
        "удалить маршрут %s" % dst_s,
        apply=lambda: _ip_rc(["route", "del", dst_s]),
        prepare=lambda: snap,
        verify=lambda: not _routes_json(dst_s),
        rollback=lambda data: _restore_routes(dst_s, data),
    )
    res = tx.run()
    if res["ok"] and insurance_grace > 0:
        tx.schedule_rollback(lambda: not _routes_json(dst_s), grace=insurance_grace)
    return res

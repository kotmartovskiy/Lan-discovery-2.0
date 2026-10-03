#!/usr/bin/env bash
# LAN Discovery — Installer 2.0 для чистых Debian/Ubuntu/Armbian (§25).
#
# Цепочка §25: preflight → hardware detection → dependencies → core →
# modules → configuration → systemd → health check.
# ARM/x86_64/minimal: без предположений о плате; опциональные пакеты
# ставятся best-effort (не роняют установку минимальной системы).
# Идемпотентен: повторный запуск поверх существующей установки ничего
# не ломает (код не перезаписывается, config/БД/юнит только дополняются).
#
# Флаги:
#   --prefix DIR     куда ставить (default: /opt/lan-discovery)
#   --unit-dir DIR   куда класть systemd-юнит (default: /etc/systemd/system)
#   --skip-apt       не трогать apt (пакеты уже есть / тестовое окружение)
#   --no-enable      не делать systemctl enable --now
#   --dry-run        только показать план, ничего не менять
#
# Запуск: sudo ./install.sh   (из каталога с кодом) либо с флагами выше.
set -euo pipefail

usage() {
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

PREFIX="/opt/lan-discovery"
UNIT_DIR="/etc/systemd/system"
SETTINGS="/etc/lan-discovery/settings.json"
USERS="/etc/lan-discovery/users.json"
SKIP_APT=0
NO_ENABLE=0
DRY_RUN=0
PY_BIN=""   # заполняется в preflight: python3, иначе python (>=3.9)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)    PREFIX="${2:?--prefix требует значение}"; shift 2 ;;
        --unit-dir)  UNIT_DIR="${2:?--unit-dir требует значение}"; shift 2 ;;
        --skip-apt)  SKIP_APT=1; shift ;;
        --no-enable) NO_ENABLE=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "[install] ERROR: неизвестный флаг: $1" >&2; usage; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARN: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

# run — в dry-run только печатает план
run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: $*"
    else
        "$@"
    fi
}

need_root() {
    # root нужен для apt и/или юнита в /etc
    if [[ $DRY_RUN -eq 1 ]]; then return 0; fi
    if [[ $SKIP_APT -eq 1 && "$UNIT_DIR" != /etc/* ]]; then return 0; fi
    [[ $EUID -eq 0 ]] || die "нужен root (sudo), либо --skip-apt с пользовательским --unit-dir"
}

# ---------------------------------------------------------------- preflight
# §25: preflight — проверка окружения БЕЗ предположений о плате:
# python, файлы-источники, дистрибутив (Debian/Ubuntu-совместимость),
# место на диске, наличие apt (warn для не-Debian систем).
step_preflight() {
    log "step: preflight"
    # python3 предпочтителен; fallback python (MSYS/нестандартные minimal)
    local cand
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 \
            && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PY_BIN="$cand"
            break
        fi
    done
    [[ -n "$PY_BIN" ]] || die "нужен Python >= 3.9 (python3/python не найдены или стары)"
    [[ -f "$SCRIPT_DIR/app.py" ]] || die "app.py не найден рядом со install.sh ($SCRIPT_DIR)"
    [[ -f "$SCRIPT_DIR/requirements.txt" ]] || die "requirements.txt не найден рядом со install.sh ($SCRIPT_DIR)"
    [[ -f "$SCRIPT_DIR/deploy/lan-discovery.service" ]] \
        || die "deploy/lan-discovery.service не найден"
    need_root
    log "  python=$PY_BIN $($PY_BIN -V 2>&1 | awk '{print $2}'), prefix=$PREFIX, unit-dir=$UNIT_DIR"
    log "  arch: $(uname -m), kernel: $(uname -r)"
    # дистрибутив: Debian/Ubuntu/Armbian-совместимость (§25), иное — WARN
    local distro_id="" distro_like=""
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        distro_id=$(. /etc/os-release && echo "${ID:-}")
        distro_like=$(. /etc/os-release && echo "${ID_LIKE:-}")
        log "  distro: ${distro_id:-?} (${distro_like:-})"
        case " $distro_id $distro_like " in
            *debian*|*ubuntu*) ;;
            *) warn "дистрибутив '$distro_id' не подтверждён как Debian/Ubuntu-compatible — продолжаем (§25), пакеты проверяются по факту" ;;
        esac
    else
        warn "/etc/os-release не найден — дистрибутив определить нельзя"
    fi
    command -v apt-get >/dev/null 2>&1 || warn "apt-get не найден: ставьте зависимости вручную либо --skip-apt"
    command -v dpkg >/dev/null 2>&1 || warn "dpkg не найден: проверка пакетов будет пропущена"
    # место на диске под префикс (минимум ~400MB с venv+пакетами)
    local avail_mb
    avail_mb=$(df -Pm "$(dirname "$PREFIX")" 2>/dev/null | awk 'NR==2{print $4}' || echo "")
    if [[ -n "$avail_mb" ]]; then
        if (( avail_mb < 400 )); then
            warn "мало места под $PREFIX: ${avail_mb}MB (рекомендуется >= 400MB)"
        else
            log "  диск: ${avail_mb}MB свободно под $(dirname "$PREFIX")"
        fi
    fi
}

# --------------------------------------------------------- hardware detection
# §25: hardware detection — переиспользуем core/hardware.detect_platform()
# (тот же источник, что /api/health; только stdlib — работает до venv).
# Отчёт: $PREFIX/hw-detect.json; не критично — при сбое только WARN.
step_hw() {
    log "step: hw — hardware detection (core/hardware, без плато-специфики)"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: $PY_BIN tools/hw_detect.py --out $PREFIX/hw-detect.json"
        return
    fi
    run mkdir -p "$PREFIX"
    if ! "$PY_BIN" "$SCRIPT_DIR/tools/hw_detect.py" --out "$PREFIX/hw-detect.json"; then
        warn "hw-detect не удался (не критично): продолжаем без отчёта"
    fi
}

# -------------------------------------------------------------------- code
step_code() {
    if [[ "$SCRIPT_DIR" -ef "$PREFIX" ]]; then
        log "step: code — код уже в $PREFIX"
        return
    fi
    if [[ -f "$PREFIX/app.py" ]]; then
        log "step: code — в $PREFIX уже есть app.py, не перезаписываю (обновление — см. docs/Обновление.md)"
        return
    fi
    log "step: code — копирую код в $PREFIX"
    run mkdir -p "$PREFIX"
    for item in app.py network_check.py core modules templates static games \
                tools deploy requirements.txt requirements-dev.txt pytest.ini \
                install.sh tests; do
        if [[ -e "$SCRIPT_DIR/$item" ]]; then
            run cp -r "$SCRIPT_DIR/$item" "$PREFIX/"
        fi
    done
}

# ------------------------------------------------------------------- deps
# §25: dependencies для Debian/Ubuntu/ARM/minimal — критичные пакеты
# обязательны (панель без них не стартует), опциональные — best-effort
# (модульная функциональность: сеть/WiFi/BT/SMART/media); неудача
# опциональных не роняет установку минимальной системы.
step_deps() {
    if [[ $SKIP_APT -eq 1 ]]; then
        log "step: deps — пропущено (--skip-apt)"
        return
    fi
    command -v apt-get >/dev/null 2>&1 || { warn "step: deps — нет apt-get, пропущено"; return; }
    # python3-cffi/cryptography/bcrypt — без wheel на armhf (armv7l):
    # pip падает на сборке cffi без компилятора, ставим из Debian в
    # системный site, venv создаётся с --system-site-packages (PHASE 4).
    local critical=(python3-venv python3-cffi python3-cryptography python3-bcrypt)
    local optional=(nmap traceroute dnsutils iw bluez smartmontools ffmpeg mpv)
    local missing_crit=() missing_opt=()
    local p
    for p in "${critical[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 || missing_crit+=("$p")
    done
    for p in "${optional[@]}"; do
        dpkg -s "$p" >/dev/null 2>&1 || missing_opt+=("$p")
    done
    if [[ ${#missing_crit[@]} -eq 0 && ${#missing_opt[@]} -eq 0 ]]; then
        log "step: deps — все пакеты уже установлены"
        return
    fi
    if [[ ${#missing_crit[@]} -gt 0 ]]; then
        log "step: deps — apt-get install ${missing_crit[*]} (критичные)"
        run apt-get update -qq
        run apt-get install -y "${missing_crit[@]}"
    fi
    if [[ ${#missing_opt[@]} -gt 0 ]]; then
        log "step: deps — опциональные: ${missing_opt[*]} (best-effort, по одному)"
        run apt-get update -qq
        local failed=()
        for p in "${missing_opt[@]}"; do
            if ! run apt-get install -y "$p"; then
                warn "опциональный пакет '$p' не установился — модуль останется без функционала"
                failed+=("$p")
            fi
        done
        if [[ ${#failed[@]} -gt 0 && -f "$PREFIX/hw-detect.json" ]]; then
            # дописываем в отчёт детекта, что не встало (для диагностики)
            "$PY_BIN" - "$PREFIX/hw-detect.json" "${failed[@]}" <<'PYEOF'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
except Exception:
    sys.exit(0)
rep["deps_missing"] = sys.argv[2:]
with open(path, "w", encoding="utf-8") as f:
    json.dump(rep, f, indent=2, ensure_ascii=False)
PYEOF
        fi
    fi
}

# ------------------------------------------------------------------ modules
# §25: modules — проверка, что builtin-манифесты находятся и читаются
# (потребитель core.module_loader.discover_modules, инвариант №5).
step_modules() {
    log "step: modules — проверка builtin-манифестов (core.module_loader)"
    run bash -c "cd '$PREFIX' && ./venv/bin/python -c 'import core.module_loader as ml; mods = ml.discover_modules(force=True); assert mods, \"нет module.json\"; print(\"[install]   builtin modules: %d\" % len(mods))'"
}

# ------------------------------------------------------------------- venv
step_venv() {
    local venv="$PREFIX/venv"
    if [[ ! -x "$venv/bin/python" ]]; then
        log "step: venv — создаю $venv (--system-site-packages: apt-пакеты cffi/cryptography/bcrypt)"
        run "$PY_BIN" -m venv --system-site-packages "$venv"
    else
        log "step: venv — уже есть"
    fi
    log "step: pip — установка зависимостей (requirements.txt)"
    run "$venv/bin/python" -m pip install --quiet -r "$PREFIX/requirements.txt"
}

# ------------------------------------------------------------------ config
step_config() {
    if [[ -f "$SETTINGS" ]]; then
        log "step: config — уже есть ($SETTINGS), не трогаю"
    elif [[ $DRY_RUN -eq 1 ]]; then
        log "step: config — создаю базовый $SETTINGS (авто-subnet из интерфейсов)"
        log "DRY: запись базового settings.json в $SETTINGS"
    else
        log "step: config — создаю базовый $SETTINGS (авто-subnet из интерфейсов)"
        mkdir -p "$(dirname "$SETTINGS")"
        "$PY_BIN" - "$SETTINGS" <<'PYEOF'
import ipaddress, json, subprocess, sys

subnet = None
self_ips = []
try:
    out = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "scope", "global"],
        capture_output=True, text=True, timeout=10,
    ).stdout
except Exception:
    out = ""
pref = "24"
for line in out.splitlines():
    parts = line.split()
    if "inet" not in parts:
        continue
    cidr = parts[parts.index("inet") + 1]
    ip, pref = cidr.split("/")
    self_ips.append(ip)
if self_ips:
    subnet = str(ipaddress.ip_network(f"{self_ips[0]}/{pref}", strict=False))

cfg = {
    "network": {
        "subnet": subnet or "192.168.3.0/24",
        "scan_interval": 30,
        "max_misses": 6,
        "self_ips": self_ips,
    },
    "web": {"flask_port": 8080},
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print(f"[install]   subnet={cfg['network']['subnet']} self_ips={self_ips}")
PYEOF
    fi

    # A-01: первый администратор. Без users.json чистая установка остаётся
    # вообще без входа (load_users() → {}): login обещан docs/2.0/INSTALL.md.
    if [[ -f "$USERS" ]]; then
        log "step: config — $USERS уже есть, не трогаю"
    elif [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: запись $USERS (логин admin)"
    else
        log "step: config — создаю $USERS (admin/1234 — сменить после первого входа)"
        mkdir -p "$(dirname "$USERS")"
        local upy="$PREFIX/venv/bin/python"
        [[ -x "$upy" ]] || upy="$PY_BIN"
        "$upy" - "$USERS" <<'PYEOF'
import hashlib, json, os, sys

path = sys.argv[1]
pw = "1234"
try:
    import bcrypt
    hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
except Exception:
    # legacy sha256: auth._verify_hash принимает, логин лениво перехеширует в bcrypt
    hashed = hashlib.sha256(pw.encode()).hexdigest()
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"admin": {"password_hash": hashed, "role": "admin",
                         "enabled": True, "display_name": "admin"}},
              f, indent=2, ensure_ascii=False)
os.replace(tmp, path)
print(f"[install]   users: admin создан")
PYEOF
    fi
}

# --------------------------------------------------------------------- db
step_db() {
    log "step: db — init схемы (миграции/ensure/retention; идемпотентно)"
    [[ "$PREFIX" == "/opt/lan-discovery" ]] \
        || warn "префикс нестандартный ($PREFIX): БД создаётся по пути из кода /opt/lan-discovery/devices.db"
    run bash -c "cd '$PREFIX' && ./venv/bin/python -c 'from modules.devices_routes import init_db_schema; init_db_schema()'"
}

# -------------------------------------------------------------------- unit
step_unit() {
    local src="$PREFIX/deploy/lan-discovery.service"
    [[ -f "$src" ]] || src="$SCRIPT_DIR/deploy/lan-discovery.service"
    local dst="$UNIT_DIR/lan-discovery.service"
    [[ -f "$src" ]] || die "нет шаблона юнита: $src"
    log "step: unit — установка $dst (ExecStart: $PREFIX/venv/...)"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: sed '{PREFIX}→$PREFIX' $src > $dst"
        return
    fi
    mkdir -p "$UNIT_DIR"
    sed "s|{PREFIX}|$PREFIX|g" "$src" > "$dst"
    if [[ "$UNIT_DIR" == /etc/* ]] && command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload
        if [[ $NO_ENABLE -eq 1 ]]; then
            log "step: unit — enable пропущен (--no-enable)"
        else
            systemctl enable --now lan-discovery
            log "step: unit — enable --now выполнен"
        fi
    else
        log "step: unit — systemd-шаг пропущен (нет systemctl или не-/etc unit-dir)"
    fi
}

# ------------------------------------------------------------------ health
# §25: health check — живой запрос /api/health через python3 urllib
# (curl НЕ требуется: на минимальной Debian его может не быть),
# валидация JSON + отчёт о версии/схеме БД. Порт — из settings.json,
# если он уже существует, иначе 8080.
step_health() {
    log "step: health — жду http://127.0.0.1:<port>/api/health (до 60 с)"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: $PY_BIN urllib GET /api/health + проверка version/db.user_version"
        return
    fi
    if ! "$PY_BIN" - "$SETTINGS" <<'PYEOF'
import json, sys, time, urllib.error, urllib.request

settings_path = sys.argv[1]
port = 8080
try:
    with open(settings_path, encoding="utf-8") as f:
        port = int(json.load(f).get("web", {}).get("flask_port", 8080))
except Exception:
    pass
url = "http://127.0.0.1:%d/api/health" % port
last = None
data = None
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.load(resp)
        break
    except Exception as e:
        last = e
        time.sleep(2)
if data is None:
    print("[install] ERROR: панель не отвечает на %s (%s); см. systemctl status / journalctl -u lan-discovery"
          % (url, last), file=sys.stderr)
    sys.exit(1)
db = data.get("db") or {}
uv = db.get("user_version")
print("[install]   health: OK, version=%s, user_version=%s"
      % (data.get("version", "?"), uv))
if not isinstance(uv, int) or uv < 3:
    print("[install] WARN: схема БД user_version=%s (ожидалась 2.0 / v3) — возможно, установлен поверх старой версии"
          % uv, file=sys.stderr)
PYEOF
    then
        die "health check не пройден — см. systemctl status lan-discovery / journalctl -u lan-discovery"
    fi
}

# ------------------------------------------------------------------ итог
summary() {
    log "----------------------------------------------------------"
    log "Готово: LAN Discovery установлен в $PREFIX"
    log "  статус:   systemctl status lan-discovery"
    log "  журнал:   journalctl -u lan-discovery -f"
    log "  панель:   http://<IP-машины>:8080  (admin/1234 — смените пароль!)"
    [[ -f "$PREFIX/hw-detect.json" ]] \
        && log "  hw-отчёт: $PREFIX/hw-detect.json"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "  (dry-run: изменения не применялись)"
    fi
    return 0
}

main() {
    log "LAN Discovery installer (§25)$([[ $DRY_RUN -eq 1 ]] && echo ' [DRY-RUN]')"
    step_preflight
    step_hw
    step_code
    step_deps
    step_venv
    step_modules
    step_config
    step_db
    step_unit
    step_health
    summary
}

main

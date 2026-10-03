#!/usr/bin/env bash
# LAN Discovery — обновление с бэкапом и авто-откатом (PHASE 10).
#
# Цикл: бэкап (код+config+БД) → apply → verify (py_compile) → pip →
# restart → health; любая ошибка на verify/health ⇒ авто-rollback к
# бэкапу этого запуска (restore кода/БД + restart + контрольный health).
#
# Флаги:
#   --from DIR       откуда брать новый код (каталог с app.py и пр.);
#                    без --from — git pull, если $PREFIX это git-репо
#   --rollback [TS]  откат к бэкапу TS (или к последнему, если TS не указан)
#   --prefix DIR     установочный каталог (default: /opt/lan-discovery)
#   --unit NAME      systemd-юнит (default: lan-discovery); health-порт —
#                    из settings web.flask_port (default 8080)
#   --keep N         сколько бэкапов хранить (default: 5)
#   --dry-run        только показать план, ничего не менять
#
# Запуск: sudo ./update.sh --from /path/to/new-code
set -euo pipefail

PREFIX="/opt/lan-discovery"
BACKUP_ROOT="/var/backups/lan-discovery"
FROM=""
ROLLBACK_TS=""
DO_ROLLBACK=0
UNIT="lan-discovery"
KEEP=5
DRY_RUN=0

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)      FROM="${2:?--from требует каталог}"; shift 2 ;;
        --rollback)  DO_ROLLBACK=1
                     if [[ "${2:-}" =~ ^[0-9]{14}$ ]]; then
                         ROLLBACK_TS="$2"; shift
                     fi
                     shift ;;
        --prefix)    PREFIX="${2:?--prefix требует значение}"; shift 2 ;;
        --unit)      UNIT="${2:?--unit требует значение}"; shift 2 ;;
        --keep)      KEEP="${2:?--keep требует число}"; shift 2 ;;
        --dry-run)   DRY_RUN=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "[update] ERROR: неизвестный флаг: $1" >&2; usage; exit 1 ;;
    esac
done

log()  { printf '[update] %s\n' "$*"; }
warn() { printf '[update] WARN: %s\n' "$*" >&2; }
die()  { printf '[update] ERROR: %s\n' "$*" >&2; exit 1; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then log "DRY: $*"; else "$@"; fi
}

[[ $EUID -eq 0 || $DRY_RUN -eq 1 ]] \
    || die "нужен root (sudo): бэкап в $BACKUP_ROOT и restart сервиса"

CODE_ITEMS=(app.py network_check.py core modules templates static games
            tools deploy requirements.txt requirements-dev.txt install.sh
            update.sh pytest.ini tests)

# ------------------------------------------------------------------ backup
backup_path() {
    ls -1 "$BACKUP_ROOT" 2>/dev/null \
        | grep -E '^20[0-9]{6}-[0-9]{6}$' | sort | tail -1
}

do_backup() {
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    local dir="$BACKUP_ROOT/$ts"
    log "step: backup — $dir"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: tar кода + settings.json + devices.db → $dir/"
        BACKUP_TS="$ts"
        return 0
    fi
    mkdir -p "$dir"
    local items=()
    for item in "${CODE_ITEMS[@]}"; do
        [[ -e "$PREFIX/$item" ]] && items+=("$item")
    done
    (cd "$PREFIX" && tar czf "$dir/code.tar.gz" "${items[@]}")
    [[ -f /etc/lan-discovery/settings.json ]] \
        && cp /etc/lan-discovery/settings.json "$dir/settings.json"
    python3 -c "
import sqlite3, sys
src = sqlite3.connect('$PREFIX/devices.db')
dst = sqlite3.connect('$dir/devices.db')
src.backup(dst)
dst.close(); src.close()
" 2>/dev/null || warn "бэкап devices.db не удался (файл отсутствует?)"
    local git_rev=""
    [[ -d "$PREFIX/.git" ]] && git_rev=$(git -C "$PREFIX" rev-parse HEAD 2>/dev/null || true)
    local app_ver=""
    # Task6: версия 2.0 живёт в core/version.py (grep по app.py находил
    # только import → в meta.json пустая строка); app.py — fallback
    app_ver=$(grep -oE 'APP_VERSION *= *"[^"]+"' "$PREFIX/core/version.py" 2>/dev/null \
        | head -1 | sed 's/.*"\(.*\)"/\1/' || true)
    [[ -n "$app_ver" ]] \
        || app_ver=$(grep -oE 'APP_VERSION *= *"[^"]+"' "$PREFIX/app.py" 2>/dev/null \
            | head -1 | sed 's/.*"\(.*\)"/\1/' || true)
    printf '{"ts": "%s", "version": "%s", "from": "%s", "git_rev": "%s"}\n' \
        "$ts" "$app_ver" "${FROM:-git}" "$git_rev" > "$dir/meta.json"
    BACKUP_TS="$ts"
    log "  код+config+БД сохранены"
    rotate_backups
}

rotate_backups() {
    local dirs
    dirs=$(ls -1d "$BACKUP_ROOT"/20* 2>/dev/null | sort)
    local n
    n=$(printf '%s\n' "$dirs" | grep -c . || true)
    if [[ "$n" -gt "$KEEP" ]]; then
        printf '%s\n' "$dirs" | head -n $((n - KEEP)) | while read -r d; do
            log "  ротация: удаляю $d"
            rm -rf "$d"
        done
    fi
}

# ---------------------------------------------------------------- rollback
do_rollback() {
    local ts="${1:-}"
    if [[ -z "$ts" ]]; then
        ts=$(backup_path)
        [[ -n "$ts" ]] || die "нет бэкапов в $BACKUP_ROOT"
    fi
    local dir="$BACKUP_ROOT/$ts"
    [[ -d "$dir" ]] || die "бэкап не найден: $dir"
    log "step: rollback — $dir"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: распаковка code.tar.gz, restore settings/devices.db"
        return 0
    fi
    (cd "$PREFIX" && tar xzf "$dir/code.tar.gz")
    [[ -f "$dir/settings.json" ]] && cp "$dir/settings.json" \
        /etc/lan-discovery/settings.json
    [[ -f "$dir/devices.db" ]] && python3 -c "
import sqlite3
src = sqlite3.connect('$dir/devices.db')
dst = sqlite3.connect('$PREFIX/devices.db')
src.backup(dst)
dst.close(); src.close()
"
    restart_and_health || return 1
    log "step: rollback — выполнен, система здорова"
}

# ------------------------------------------------------------------- apply
do_apply() {
    if [[ -n "$FROM" ]]; then
        [[ -f "$FROM/app.py" ]] || die "в $FROM нет app.py"
        log "step: apply — копирую код из $FROM"
        local item
        for item in "${CODE_ITEMS[@]}"; do
            [[ -e "$FROM/$item" ]] || continue
            run cp -r "$FROM/$item" "$PREFIX/"
        done
    elif [[ -d "$PREFIX/.git" ]]; then
        log "step: apply — git pull --ff-only"
        run git -C "$PREFIX" pull --ff-only
    else
        die "укажите --from DIR (каталог не является git-репо)"
    fi
}

# ------------------------------------------------------------------ verify
do_verify() {
    log "step: verify — py_compile app.py + core/ + modules/"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: python -m py_compile app.py core/*.py modules/*.py"
        return 0
    fi
    "$PREFIX/venv/bin/python" - "$PREFIX" <<'EOF' || return 1
import pathlib, py_compile, sys
root = pathlib.Path(sys.argv[1])
files = [root / "app.py"]
files += sorted((root / "core").rglob("*.py"))
files += sorted((root / "modules").rglob("*.py"))
bad = []
for f in files:
    try:
        py_compile.compile(str(f), doraise=True)
    except Exception as e:
        bad.append(f"{f}: {e}")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
print(f"SYNTAX OK ({len(files)} files)")
EOF
}

do_pip() {
    log "step: pip — requirements.txt"
    run "$PREFIX/venv/bin/python" -m pip install --quiet \
        -r "$PREFIX/requirements.txt"
}

# Порт панели — из settings.json (web.flask_port, default 8080).
# Хардкод 8080 ошибочно проверял ЧУЖУЮ панель: на стенде 2.0 живёт на
# 8090, а 1.1 отвечает на 8080 — health давал ложный success/провал.
health_port() {
    python3 -c "import json; print(int(json.load(open('/etc/lan-discovery/settings.json')).get('web', {}).get('flask_port') or 8080))" \
        2>/dev/null || echo 8080
}

restart_and_health() {
    log "step: restart — systemctl restart $UNIT"
    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY: systemctl restart $UNIT"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl restart "$UNIT"
    else
        warn "systemctl отсутствует — рестарт пропущен"
    fi
    local port
    port=$(health_port)
    log "step: health — жду http://127.0.0.1:$port/api/health (до 60 с)"
    local i
    for i in $(seq 1 30); do
        if curl -fsS --max-time 3 "http://127.0.0.1:$port/api/health" \
                >/dev/null 2>&1; then
            log "step: health — OK"
            return 0
        fi
        sleep 2
    done
    warn "health не прошёл"
    return 1
}

auto_rollback() {
    local reason="$1"
    warn "авто-rollback: $reason"
    if [[ ${BACKUP_TS:-} == "" ]]; then
        warn "бэкап не создан — откат невозможен"
        return 1
    fi
    do_rollback "$BACKUP_TS"
}

main() {
    log "LAN Discovery updater$([[ $DRY_RUN -eq 1 ]] && echo ' [DRY-RUN]')"
    if [[ $DO_ROLLBACK -eq 1 ]]; then
        do_rollback "$ROLLBACK_TS"
        log "Готово: откат выполнен"
        return 0
    fi

    BACKUP_TS=""
    do_backup
    do_apply
    if ! do_verify; then
        auto_rollback "py_compile не прошёл" || true
        die "verify провалился — выполнен авто-rollback к ${BACKUP_TS} (исходный код восстановлен); почините источник и повторите"
    fi
    do_pip
    if ! restart_and_health; then
        if auto_rollback "health не прошёл"; then
            die "обновление откатено к ${BACKUP_TS}; система здорова"
        fi
        die "health не прошёл И откат не удался — срочная диагностика!"
    fi
    log "----------------------------------------------------------"
    log "Готово: обновление применено, бэкап: $BACKUP_ROOT/$BACKUP_TS"
    log "  откат вручную: sudo ./update.sh --rollback $BACKUP_TS"
    return 0
}

main

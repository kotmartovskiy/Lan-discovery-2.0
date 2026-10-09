#!/bin/bash
# LTE-failover watchdog (фаза 5): следит за связностью LAN-интернета;
# при полном обрыве поднимает NM-профиль LTE, при устойчивом восстановлении
# снимает. Без профиля LTE — режим наблюдения (пишет статус в STATE_FILE).
#
# Устойчивость (требование юзера: долгие обрывы, огромный пинг, низкая
# скорость): переходы только по серии подряд проб — медленный, но живой
# интернет НЕ считается обрывом (icmp-проба с длинным таймаутом).
#
# Конфиг (опционален): /etc/lan-discovery/failover.conf
set -u

CONF=/etc/lan-discovery/failover.conf
PROBE_GW=${PROBE_GW:-192.168.3.1}
PROBE_INET=${PROBE_INET:-1.1.1.1}
PROBE_TIMEOUT=${PROBE_TIMEOUT:-8}
LTE_CON=${LTE_CON:-lte}
FAIL_THRESHOLD=${FAIL_THRESHOLD:-3}
OK_THRESHOLD=${OK_THRESHOLD:-3}
INTERVAL=${INTERVAL:-10}
STATE_FILE=${STATE_FILE:-/run/lan-failover.state}
[ -r "$CONF" ] && . "$CONF"

fail=0
ok=0
state="LAN"
lte_active=0

log() { echo "$(date '+%d.%m.%Y %H:%M:%S') $*"; }

set_state() {
  if [ "$1" != "$state" ]; then
    state=$1
    echo "$state" > "$STATE_FILE"
    log "STATE -> $state"
  fi
}

net_ok() {
  ping -c1 -W "$PROBE_TIMEOUT" "$PROBE_GW" >/dev/null 2>&1 &&
  ping -c1 -W "$PROBE_TIMEOUT" "$PROBE_INET" >/dev/null 2>&1
}

lte_exists() { nmcli -t con show id "$LTE_CON" >/dev/null 2>&1; }

lte_up() {
  if ! lte_exists; then
    log "LTE profile '$LTE_CON' not found (observation only)"
    return 1
  fi
  if nmcli -w 20 con up id "$LTE_CON" >/dev/null 2>&1; then
    lte_active=1
    log "LTE UP ($LTE_CON)"
  else
    log "LTE up FAILED ($LTE_CON)"
  fi
}

lte_down() {
  [ "$lte_active" = 1 ] || return 0
  nmcli -w 20 con down id "$LTE_CON" >/dev/null 2>&1 && log "LTE DOWN ($LTE_CON)"
  lte_active=0
}

echo "LAN" > "$STATE_FILE"
log "watchdog start gw=$PROBE_GW inet=$PROBE_INET timeout=${PROBE_TIMEOUT}s lte='$LTE_CON' fail>=$FAIL_THRESHOLD ok>=$OK_THRESHOLD"

while true; do
  if net_ok; then
    fail=0
    if [ "$state" != "LAN" ]; then
      ok=$((ok + 1))
      if [ "$ok" -ge "$OK_THRESHOLD" ]; then
        lte_down
        set_state LAN
        ok=0
      fi
    else
      ok=0
    fi
  else
    ok=0
    fail=$((fail + 1))
    if [ "$fail" -ge "$FAIL_THRESHOLD" ] && [ "$state" != "LTE" ]; then
      set_state LTE
      lte_up
    fi
  fi
  sleep "$INTERVAL"
done

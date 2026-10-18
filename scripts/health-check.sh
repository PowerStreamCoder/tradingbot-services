#!/bin/bash
#
# VM health check for the trading bot system.
#
# Runs from the i030983 crontab every 5 minutes (wrapped in cron-run.sh, see the
# services README). It produces three things on purpose:
#
#   1. $HOME/logs/health-check.log — one summary line per run, for `tail -f`.
#   2. $HOME/logs/health-alerts.log — durable local record of each fault onset
#      and its recovery, for an operator already on the box.
#   3. Email, via tradingbots/services/notify_alert.py — so an operator who is
#      NOT watching the box learns about it. Delivery is best-effort: a broken
#      mail path must not change this script's behaviour.
#
# Design notes
# ------------
# * This script always exits 0. It is a *reporter*, not a systemd unit: a
#   non-zero exit would make cron report a "failure" for a perfectly faithful
#   observation, and health-check.log would drown in noise. Faults are reported
#   with their own severity, and repeated runs are throttled to one email per
#   fault per hour by notify_alert.py (via --min-interval).
# * A successful check that previously failed emits a one-off RECOVERED notice,
#   tracked with a marker file per check under logs/health-state/.
# * Checks are independent, so one broken dependency cannot mask the rest. The
#   two obvious cascades (docker down → skip container/port; container down →
#   skip port) are short-circuited so the operator gets the ROOT cause rather
#   than three copies of one symptom.
# * Set HEALTH_CHECK_DRY_RUN=1 to print alerts instead of sending them. Used to
#   verify the wiring without emailing anybody.
#
# systemd unit failures are covered separately by service-failure-notifier@.service
# (see OnFailure= on the units) — this script only checks the VM's state.

set -uo pipefail

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
ALERT_FILE="$LOG_DIR/health-alerts.log"
LOG_FILE="$LOG_DIR/health-check.log"
STATE_DIR="$LOG_DIR/health-state"
mkdir -p "$STATE_DIR"

PYTHON_BIN="${PYTHON_BIN:-/home/i030983/venv311/bin/python3}"
NOTIFY_CLI="${NOTIFY_CLI:-/home/i030983/tradingbots/services/notify_alert.py}"
IB_PORT="${IB_PORT:-4002}"
DISK_THRESHOLD_PCT="${DISK_THRESHOLD_PCT:-80}"
MEM_AVAIL_FLOOR_PCT="${MEM_AVAIL_FLOOR_PCT:-10}"
REPEAT_INTERVAL_SECONDS="${REPEAT_INTERVAL_SECONDS:-3600}"

DRY_RUN_FLAG=""
if [ "${HEALTH_CHECK_DRY_RUN:-0}" = "1" ]; then
    DRY_RUN_FLAG="--dry-run"
fi

ALERTS=0

# ISO-8601 local time with UTC offset. Written with an explicit format rather
# than GNU's `date -Is` so the script can also be exercised off the VM (BSD date
# rejects -Is), which is how the branches below were tested before deployment.
now() {
    date +%Y-%m-%dT%H:%M:%S%z
}

log() {
    echo "[$(now)] $*" >> "$LOG_FILE"
}

# email_alert <priority> <dedup-key> <title> <message> [min-interval]
# Hands off to the pipeline CLI. Never fatal: monitoring must not fall over when
# mail does.
email_alert() {
    local priority="$1" key="$2" title="$3" message="$4" interval="${5:-$REPEAT_INTERVAL_SECONDS}"
    "$PYTHON_BIN" "$NOTIFY_CLI" \
        --priority "$priority" \
        --source vm-health \
        --title "$title" \
        --message "$message" \
        --dedup-key "$key" \
        --min-interval "$interval" \
        $DRY_RUN_FLAG >> "$LOG_FILE" 2>&1 || true
}

# report <ok 0|1> <key> <priority> <title> <message>
# ok=0 clears the fault (announcing recovery if it was standing); ok=1 records the
# fault onset locally and re-arms the throttled email on every run.
report() {
    local ok="$1" key="$2" priority="$3" title="$4" message="$5"
    local marker="$STATE_DIR/$key"

    if [ "$ok" -eq 0 ]; then
        if [ -f "$marker" ]; then
            rm -f "$marker"
            local line="RECOVERED: $title (cleared $(now))"
            echo "[$(now)] $line" | tee -a "$ALERT_FILE"
            # One-off transition, so no throttle window.
            email_alert "medium" "vm-health:recovered:$key" "RECOVERED: $title" "$line" 0
        fi
        return 0
    fi

    ALERTS=$((ALERTS + 1))
    if [ ! -f "$marker" ]; then
        : > "$marker"
        echo "[$(now)] FAIL [$priority] $title :: $message" | tee -a "$ALERT_FILE"
    fi
    email_alert "$priority" "vm-health:$key" "$title" "$message"
}

log "health check start"

# 1. Docker daemon ------------------------------------------------------------
docker_ok=1
docker ps > /dev/null 2>&1 && docker_ok=0
report "$docker_ok" docker critical \
    "Docker daemon is not running" \
    "The docker daemon is not answering 'docker ps'. Every container, including IB Gateway, is down."
if [ "$docker_ok" -ne 0 ]; then
    log "health check done: alerts=$ALERTS (docker down; container/port checks skipped)"
    exit 0
fi

# 2. IB Gateway container -----------------------------------------------------
gw_ok=1
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway" && gw_ok=0
report "$gw_ok" ib-gateway critical \
    "IB Gateway container is not running" \
    "The 'ib-gateway' container is absent from 'docker ps'. The bots cannot reach the broker."
if [ "$gw_ok" -ne 0 ]; then
    log "health check done: alerts=$ALERTS (ib-gateway down; port check skipped)"
    exit 0
fi

# 3. IB Gateway API port ------------------------------------------------------
port_ok=1
ss -tln 2>/dev/null | grep -qE "[:.]${IB_PORT}[[:space:]]" && port_ok=0
report "$port_ok" ib-gateway-port critical \
    "IB Gateway API port ${IB_PORT} is not listening" \
    "Nothing is listening on tcp/${IB_PORT}: the container is up but not serving the API. Bots will fail to connect."

# 4. Core background services -------------------------------------------------
if systemctl is-active --quiet bot-control-api.service; then
    report 0 bot-control-api high "Bot Control API is not active" ""
else
    report 1 bot-control-api high \
        "Bot Control API is not active" \
        "systemd unit bot-control-api.service is not active (systemctl status bot-control-api)."
fi

if systemctl is-active --quiet notification-service.service; then
    report 0 notification-service high "Notification Service is not active" ""
else
    report 1 notification-service high \
        "Notification Service is not active" \
        "systemd unit notification-service.service is not active (systemctl status notification-service)."
fi

# 5. Disk ---------------------------------------------------------------------
disk_pct=$(df -P / | awk 'NR==2 {gsub(/%/, "", $5); print $5}')
disk_pct=${disk_pct:-0}
if [ "$disk_pct" -gt "$DISK_THRESHOLD_PCT" ]; then
    report 1 disk high \
        "Root filesystem usage is above ${DISK_THRESHOLD_PCT}%" \
        "Root filesystem is at ${disk_pct}% ($(df -h / | awk 'NR==2 {print $4}') free). Prune logs/backups before it fills."
else
    report 0 disk high "Root filesystem usage is above ${DISK_THRESHOLD_PCT}%" ""
fi

# 6. Memory -------------------------------------------------------------------
# Read /proc/meminfo defensively. Under `set -u` an unset value here would abort
# the whole check, and a monitoring script that dies silently is worse than one
# that reports nothing. Anything unreadable degrades to "no alert".
mem_total=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true)
mem_avail=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || true)
mem_total=${mem_total:-0}
mem_avail=${mem_avail:-0}
if [ "$mem_total" -gt 0 ]; then
    mem_avail_pct=$(( mem_avail * 100 / mem_total ))
else
    mem_avail_pct=100
    log "note: could not read memory information from /proc/meminfo"
fi
swap_used_mb=$(free -m 2>/dev/null | awk '/^Swap:/ {print $3}' || true)
swap_used_mb=${swap_used_mb:-unknown}

if [ "$mem_avail_pct" -lt "$MEM_AVAIL_FLOOR_PCT" ]; then
    report 1 memory high \
        "Available memory is below ${MEM_AVAIL_FLOOR_PCT}%" \
        "Only ${mem_avail_pct}% of RAM is available (${mem_avail} MB of ${mem_total} MB; swap in use: ${swap_used_mb} MB). The VM is close to OOM (issue #19)."
else
    report 0 memory high "Available memory is below ${MEM_AVAIL_FLOOR_PCT}%" ""
fi

log "health check done: alerts=$ALERTS disk=${disk_pct}% mem_avail=${mem_avail_pct}% swap_used=${swap_used_mb}MB"
exit 0

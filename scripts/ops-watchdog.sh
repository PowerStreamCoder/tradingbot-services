#!/bin/bash
#
# Operational watchdog for the trading bot VM (issue #310).
#
# Complements health-check.sh. Where that script watches live infrastructure
# (docker, IB Gateway, core services, disk, memory), this one watches the
# *silent* failure modes a VM audit surfaced — faults that previously produced
# no alert at all:
#
#   1. Stale lock files — a crashed job leaves /tmp/<job>.lock behind, and every
#      later run aborts "already running". This skipped 4 trading sessions of the
#      trade auditor with no notice (issue #98).
#   2. Missed scheduled runs (dead-man's snitch) — a timer that silently stops
#      firing (auditor, nightly backup, learning analysis).
#   3. Swap saturation — health-check.sh watches available RAM %; sustained swap
#      is a distinct early-OOM signal (350 MB seen in the audit).
#   4. Ownership drift — data/config dirs becoming root-owned, which breaks the
#      bot user's runtime writes (issue #297).
#   5. Filesystem hygiene — accumulating .backup_*/.bak files, oversized/old
#      backup snapshots, and stray *.out redirection files.
#
# It reuses health-check.sh's contract exactly: ALWAYS exits 0 (it is a reporter,
# not a unit whose failure means anything), one email per fault per hour via
# notify_alert.py --min-interval, onset/recovery tracked with marker files under
# logs/health-state/, and HEALTH_CHECK_DRY_RUN-style dry run. At the end it also
# publishes a compact snapshot to Firestore (system_health/current) so the
# notification service can fold VM health into the 16:00 ET daily digest; that
# publish is best-effort and never affects alerting.
#
# Set OPS_WATCHDOG_DRY_RUN=1 to print alerts instead of sending them (used to
# verify the wiring off the VM — the Linux-only checks degrade to "no alert").

set -uo pipefail

LOG_DIR="${OPS_WATCHDOG_LOG_DIR:-$HOME/logs}"
mkdir -p "$LOG_DIR"
ALERT_FILE="$LOG_DIR/health-alerts.log"
LOG_FILE="$LOG_DIR/ops-watchdog.log"
STATE_DIR="$LOG_DIR/health-state"
mkdir -p "$STATE_DIR"

PYTHON_BIN="${PYTHON_BIN:-/home/i030983/venv311/bin/python3}"
NOTIFY_CLI="${NOTIFY_CLI:-/home/i030983/tradingbots/services/notify_alert.py}"
SNAPSHOT_CLI="${SNAPSHOT_CLI:-/home/i030983/tradingbots/services/publish_health_snapshot.py}"

BOT_USER="${BOT_USER:-i030983}"
# Scan roots. Overridable so the script can be exercised off the VM against a
# temp fixture tree rather than the real home directory.
LOCK_SCAN_DIR="${LOCK_SCAN_DIR:-/tmp}"
DATA_DIRS="${DATA_DIRS:-$HOME/tradingbots/data $HOME/tradingbot-config}"
HYGIENE_DIRS="${HYGIENE_DIRS:-$HOME/tradingbots $HOME/tradingbot-config}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"

LOCK_MAX_AGE_MIN="${LOCK_MAX_AGE_MIN:-60}"
SWAP_WARN_MB="${SWAP_WARN_MB:-600}"
BACKUP_MAX_GB="${BACKUP_MAX_GB:-5}"
BACKUP_MAX_AGE_DAYS="${BACKUP_MAX_AGE_DAYS:-30}"
BACKUP_MAX_HOURS="${BACKUP_MAX_HOURS:-50}"
TELEMETRY_RETENTION_DAYS="${TELEMETRY_RETENTION_DAYS:-14}"
REPEAT_INTERVAL_SECONDS="${REPEAT_INTERVAL_SECONDS:-3600}"
IB_PAPER_PORT="${IB_PAPER_PORT:-4002}"
IB_LIVE_PORT="${IB_LIVE_PORT:-4001}"
CHECK_LIVE_GATEWAY="${CHECK_LIVE_GATEWAY:-auto}"
LIVE_GATEWAY_MARKET_HOURS_ONLY="${LIVE_GATEWAY_MARKET_HOURS_ONLY:-1}"

DRY_RUN_FLAG=""
if [ "${OPS_WATCHDOG_DRY_RUN:-0}" = "1" ]; then
    DRY_RUN_FLAG="--dry-run"
fi

# Snapshot fields published to Firestore at the end. Start from "unknown" so a
# check skipped on a non-Linux dev box publishes null rather than a false "OK".
SNAP_DISK=""
SNAP_SWAP=""
SNAP_STALE_LOCKS=0
SNAP_MISSED_RUNS=0
SNAP_BACKUPS_OK=1
SNAP_OWNERSHIP_OK=1

now() {
    date +%Y-%m-%dT%H:%M:%S%z
}

log() {
    echo "[$(now)] $*" >> "$LOG_FILE"
}

# Portable file mtime in epoch seconds. GNU coreutils and BSD/macOS `stat` take
# different flags; try both so the script runs identically in CI, on macOS, and
# on the VM. Prints nothing (and returns 1) when the path is unreadable.
file_mtime() {
    local path="$1"
    stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null
}

# Check if a TCP port is listening locally (portable across Linux ss and macOS/BSD lsof/netstat)
is_port_listening() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | grep -qE "[:.]${port}[[:space:]]"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -qE "[:.]${port}[[:space:]].*LISTEN"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import socket; s = socket.socket(); s.settimeout(0.5); res = s.connect_ex(('127.0.0.1', $port)); s.close(); exit(0 if res == 0 else 1)" 2>/dev/null
    else
        return 1
    fi
}

# Check if current time is within US regular live trading window (Mon-Fri 09:00 - 17:00 ET).
# Off-hours gateway shutdowns (e.g. IBKR nightly reset ~00:00-01:00 UTC) are expected.
is_live_trading_window() {
    if [ "${LIVE_GATEWAY_MARKET_HOURS_ONLY:-1}" != "1" ]; then
        return 0
    fi
    local dow hour_min
    dow=$(TZ="America/New_York" date +%u 2>/dev/null || date +%u)
    hour_min=$(TZ="America/New_York" date +%H%M 2>/dev/null || date +%H%M)
    # Monday(1) through Friday(5), 09:00 through 17:00 ET
    if (( dow >= 1 && dow <= 5 )) && (( 10#$hour_min >= 900 && 10#$hour_min <= 1700 )); then
        return 0
    fi
    return 1
}

# email_alert <priority> <dedup-key> <title> <message> [min-interval]
email_alert() {
    local priority="$1" key="$2" title="$3" message="$4" interval="${5:-$REPEAT_INTERVAL_SECONDS}"
    "$PYTHON_BIN" "$NOTIFY_CLI" \
        --priority "$priority" \
        --source vm-ops \
        --title "$title" \
        --message "$message" \
        --dedup-key "$key" \
        --min-interval "$interval" \
        $DRY_RUN_FLAG >> "$LOG_FILE" 2>&1 || true
}

# report <ok 0|1> <key> <priority> <title> <message>
# Mirrors health-check.sh: ok=0 clears a standing fault (one-off RECOVERED
# notice); ok=1 records onset locally and re-arms the throttled email.
report() {
    local ok="$1" key="$2" priority="$3" title="$4" message="$5"
    local marker="$STATE_DIR/$key"

    if [ "$ok" -eq 0 ]; then
        if [ -f "$marker" ]; then
            rm -f "$marker"
            local line="RECOVERED: $title (cleared $(now))"
            echo "[$(now)] $line" | tee -a "$ALERT_FILE"
            email_alert "medium" "vm-ops:recovered:$key" "RECOVERED: $title" "$line" 0
        fi
        return 0
    fi

    if [ ! -f "$marker" ]; then
        : > "$marker"
        echo "[$(now)] FAIL [$priority] $title :: $message" | tee -a "$ALERT_FILE"
    fi
    email_alert "$priority" "vm-ops:$key" "$title" "$message"
}

log "ops watchdog start"

# 1. Stale lock files ---------------------------------------------------------
# A lock older than LOCK_MAX_AGE_MIN whose PID is no longer alive (or whose file
# is empty) is stale and must be cleared; a lock that old but held by a LIVE pid
# is a legitimately long-running job and only warrants a heads-up. The PID is
# read from the first whitespace-delimited token: flock-style locks may be empty,
# pidfile-style locks carry the pid on line one.
now_epoch=$(date +%s)
dow=$(date +%u)
weekend_extra=0
if [ "$dow" -eq 6 ] || [ "$dow" -eq 7 ] || [ "$dow" -eq 1 ]; then
    weekend_extra=52
fi
lock_age_limit=$(( LOCK_MAX_AGE_MIN * 60 ))
stale_lock_count=0
if [ -d "$LOCK_SCAN_DIR" ]; then
    # Clear standing stale-lock faults whose lock file has since been removed:
    # a deleted lock is the normal resolution, but it means the for-loop below
    # never visits that key to call `report 0`, so announce recovery here.
    for marker in "$STATE_DIR"/stale-lock:*; do
        [ -e "$marker" ] || continue
        lockname=${marker##*/stale-lock:}
        if [ ! -e "$LOCK_SCAN_DIR/$lockname" ]; then
            report 0 "stale-lock:$lockname" critical "Stale lock file: $LOCK_SCAN_DIR/$lockname" ""
        fi
    done
    for lock in "$LOCK_SCAN_DIR"/*.lock; do
        [ -e "$lock" ] || continue
        mtime=$(file_mtime "$lock")
        [ -n "$mtime" ] || continue
        age=$(( now_epoch - mtime ))
        key="stale-lock:$(basename "$lock")"
        if [ "$age" -le "$lock_age_limit" ]; then
            report 0 "$key" critical "Stale lock file: $lock" ""
            continue
        fi
        pid=$(awk 'NR==1{print $1}' "$lock" 2>/dev/null)
        alive=1
        if [ -n "${pid:-}" ] && printf '%s' "$pid" | grep -qE '^[0-9]+$'; then
            # Existence, not ownership: `kill -0` returns non-zero for BOTH a
            # dead process (ESRCH) and a live one we may not signal (EPERM), so a
            # process owned by another user would be misread as dead. `ps -p`
            # answers "does this pid exist" regardless of owner, portably across
            # GNU/BSD. Fall back to kill -0 only if ps is unavailable.
            if command -v ps >/dev/null 2>&1; then
                ps -p "$pid" >/dev/null 2>&1 || alive=0
            else
                kill -0 "$pid" 2>/dev/null || alive=0
            fi
        else
            alive=0  # empty/garbage lock contents: treat as not-held
        fi
        age_min=$(( age / 60 ))
        if [ "$alive" -eq 0 ]; then
            # If the process is dead and no process holds an exclusive flock on the file,
            # clean up the abandoned lock file so it does not block future runs.
            cleaned=0
            if command -v flock >/dev/null 2>&1; then
                if flock -n "$lock" true 2>/dev/null; then
                    rm -f "$lock" 2>/dev/null && cleaned=1
                    log "cleaned up abandoned stale lock file: $lock (pid ${pid:-none} dead)"
                fi
            fi
            stale_lock_count=$(( stale_lock_count + 1 ))
            if [ "$cleaned" -eq 1 ]; then
                report 1 "$key" critical \
                    "Stale lock file cleaned: $lock (${age_min}m old, holder dead)" \
                    "Lock '$lock' was ${age_min} minutes old and its PID (${pid:-none}) is dead. The unheld lock file was automatically cleaned up to prevent blocking future runs."
            else
                report 1 "$key" critical \
                    "Stale lock file: $lock (${age_min}m old, holder dead)" \
                    "Lock '$lock' is ${age_min} minutes old and its PID (${pid:-none}) is not running. A crashed job left it behind; new runs will abort as 'already running' (issue #98). Remediation: rm -f '$lock' and restart the owning unit (e.g. systemctl restart trade-log-auditor)."
            fi
        else
            report 0 "$key" critical "Stale lock file: $lock" ""
            log "note: lock $lock is ${age_min}m old but held by live pid $pid (long-running job)"
        fi
    done
fi
SNAP_STALE_LOCKS=$stale_lock_count

# 2. Missed scheduled runs (dead-man's snitch) --------------------------------
# A systemd timer that silently stops firing leaves a real gap. Compare each
# timer's last trigger against a max-staleness window. systemctl is Linux-only;
# where it is absent (dev boxes) the check degrades to no alert.
missed_count=0
if command -v systemctl >/dev/null 2>&1; then
    # unit : max hours since last activation before we alert.
    check_timer() {
        local unit="$1" max_hours="$2"
        local ts key
        ts=$(systemctl show -p LastTriggerUSec --value "$unit" 2>/dev/null)
        key="missed-run:$unit"
        # Never triggered yet (freshly installed) or unknown: do not alert.
        if [ -z "$ts" ] || [ "$ts" = "0" ] || [ "$ts" = "n/a" ]; then
            report 0 "$key" high "Scheduled job has not run: $unit" ""
            return
        fi
        local last_epoch
        last_epoch=$(date -d "$ts" +%s 2>/dev/null || echo "")
        if [ -z "$last_epoch" ]; then
            report 0 "$key" high "Scheduled job has not run: $unit" ""
            return
        fi
        local age_h=$(( (now_epoch - last_epoch) / 3600 ))
        if [ "$age_h" -gt "$max_hours" ]; then
            missed_count=$(( missed_count + 1 ))
            report 1 "$key" high \
                "Scheduled job has not run: $unit (${age_h}h ago)" \
                "Timer '$unit' last fired ${age_h}h ago (limit ${max_hours}h). The job may have stopped silently. Remediation: systemctl list-timers '$unit'; systemctl status '${unit%.timer}.service'."
        else
            report 0 "$key" high "Scheduled job has not run: $unit" ""
        fi
    }
    # For weekday-only timers (trade-log-auditor, learning-analysis), Friday afternoon/night
    # to Monday afternoon/night is ~72h. On Saturday (6), Sunday (7), and Monday (1), weekend_extra
    # (+52h) is added so normal weekend gaps do not trigger false positive alerts.
    auditor_limit=$(( ${AUDITOR_MAX_HOURS:-26} + weekend_extra ))
    learning_limit=$(( ${LEARNING_MAX_HOURS:-50} + weekend_extra ))
    check_timer "trade-log-auditor.timer" "$auditor_limit"
    check_timer "trading-data-backup.timer" "$(( ${BACKUP_MAX_HOURS:-50} + weekend_extra ))"
    check_timer "learning-analysis.timer" "$learning_limit"
fi
SNAP_MISSED_RUNS=$missed_count

# 3. Swap saturation ----------------------------------------------------------
if command -v free >/dev/null 2>&1; then
    swap_used_mb=$(free -m 2>/dev/null | awk '/^Swap:/ {print $3}')
    swap_used_mb=${swap_used_mb:-0}
    SNAP_SWAP=$swap_used_mb
    mem_avail_mb=$(free -m 2>/dev/null | awk '/^Mem:/ {print $7}')
    if [ "$swap_used_mb" -gt "$SWAP_WARN_MB" ]; then
        report 1 swap high \
            "Swap usage above ${SWAP_WARN_MB} MB" \
            "Swap in use is ${swap_used_mb} MB (threshold ${SWAP_WARN_MB} MB; available RAM: ${mem_avail_mb:-n/a} MB). Sustained swap is an early OOM signal on this VM (issue #19). Check top memory consumers: ps -eo pid,rss,cmd --sort=-rss | head."
    else
        report 0 swap high "Swap usage above ${SWAP_WARN_MB} MB" ""
    fi
fi

# 3b. Dual Broker Gateways & Ports (Phase 1 watchdog monitoring) ---------------
# Watches gateway containers and API ports to correlate with swap/memory pressure
# and detect silent container crashes (Issue #109).
if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
    # Paper gateway container & port
    gw_paper_ok=1
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qE "^(ib-gateway|ib-gateway-paper)$" && gw_paper_ok=0
    if [ "$gw_paper_ok" -eq 0 ]; then
        report 0 "ib-gateway-container" critical "IB Gateway container (Paper) is not running" ""
        port_paper_ok=1
        is_port_listening "${IB_PAPER_PORT}" && port_paper_ok=0
        if [ "$port_paper_ok" -eq 0 ]; then
            report 0 "ib-gateway-port" critical "IB Gateway API port ${IB_PAPER_PORT} (Paper) is not listening" ""
        else
            report 1 "ib-gateway-port" critical \
                "IB Gateway API port ${IB_PAPER_PORT} (Paper) is not listening" \
                "The paper gateway container is running but port ${IB_PAPER_PORT} is not listening."
        fi
    else
        report 1 "ib-gateway-container" critical \
            "IB Gateway container (Paper) is not running" \
            "The 'ib-gateway' container is absent from 'docker ps'. Paper bots cannot reach the broker."
    fi

    # Live gateway container & port (auto-detected or enabled via CHECK_LIVE_GATEWAY=1)
    should_check_live_gw=0
    if [ "$CHECK_LIVE_GATEWAY" = "1" ]; then
        should_check_live_gw=1
    elif [ "$CHECK_LIVE_GATEWAY" = "auto" ]; then
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway-live"; then
            should_check_live_gw=1
        fi
    fi

    if [ "$should_check_live_gw" -eq 1 ]; then
        if ! is_live_trading_window; then
            # Outside live trading window (Mon-Fri 09:00-17:00 ET): live gateway is
            # legitimately stopped/idle (with --restart no) and IBKR resets nightly.
            # Clear any standing live-gateway fault markers so off-hours state is clean.
            rm -f "$STATE_DIR/ib-gateway-live-container" "$STATE_DIR/ib-gateway-live-port" 2>/dev/null || true
            log "outside live trading window (Mon-Fri 09:00-17:00 ET); skipping live gateway checks"
        else
            gw_live_ok=1
            docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway-live" && gw_live_ok=0
            if [ "$gw_live_ok" -eq 0 ]; then
                report 0 "ib-gateway-live-container" critical "IB Gateway Live container is not running" ""
                port_live_ok=1
                is_port_listening "${IB_LIVE_PORT}" && port_live_ok=0
                if [ "$port_live_ok" -eq 0 ]; then
                    report 0 "ib-gateway-live-port" critical "IB Gateway Live API port ${IB_LIVE_PORT} is not listening" ""
                else
                    report 1 "ib-gateway-live-port" critical \
                        "IB Gateway Live API port ${IB_LIVE_PORT} is not listening" \
                        "The live gateway container is running but port ${IB_LIVE_PORT} is not listening."
                fi
            else
                report 1 "ib-gateway-live-container" critical \
                    "IB Gateway Live container is not running" \
                    "The 'ib-gateway-live' container is absent from 'docker ps'. Live bots cannot reach the broker."
            fi
        fi
    fi
fi

# Capture disk % for the snapshot (health-check.sh owns the disk ALERT; here we
# only read it so the daily digest can show the number).
if command -v df >/dev/null 2>&1; then
    SNAP_DISK=$(df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')
fi

# 4. Ownership drift ----------------------------------------------------------
# Any path under the runtime data/config trees not owned by the bot user breaks
# the bot's writes (issue #297). `find ! -user` is portable across GNU/BSD.
ownership_bad=0
offenders=""
for dir in $DATA_DIRS; do
    [ -d "$dir" ] || continue
    hit=$(find "$dir" ! -user "$BOT_USER" -print 2>/dev/null | head -n 5)
    if [ -n "$hit" ]; then
        ownership_bad=1
        offenders="$offenders"$'\n'"$hit"
    fi
done
if [ "$ownership_bad" -eq 1 ]; then
    SNAP_OWNERSHIP_OK=0
    report 1 ownership high \
        "Runtime files not owned by ${BOT_USER}" \
        "Files under the data/config trees are not owned by '${BOT_USER}', which breaks runtime writes (issue #297). Offenders (first 5):${offenders}
Remediation: sudo chown -R ${BOT_USER}:${BOT_USER} ${DATA_DIRS}."
else
    report 0 ownership high "Runtime files not owned by ${BOT_USER}" ""
fi

# 5. Filesystem hygiene (once per day) ----------------------------------------
# These scans are cheaper than a trade but still pointless to run every 15 min.
# Gate to the first run of each calendar day using a dated marker.
HYGIENE_MARKER="$STATE_DIR/hygiene-last-run-date"
today=$(date +%Y-%m-%d)
run_hygiene=1
if [ -f "$HYGIENE_MARKER" ] && [ "$(cat "$HYGIENE_MARKER" 2>/dev/null)" = "$today" ]; then
    run_hygiene=0
fi
backups_ok=1
if [ "$run_hygiene" -eq 1 ]; then
    echo "$today" > "$HYGIENE_MARKER"

    # 5a. Inline backup / stray output files in source & config trees.
    cruft=""
    for dir in $HYGIENE_DIRS; do
        [ -d "$dir" ] || continue
        hit=$(find "$dir" \( -name '*.backup_*' -o -name '*.bak' -o -name '*.out' \) \
              -not -path '*/.git/*' -print 2>/dev/null | head -n 10)
        [ -n "$hit" ] && cruft="$cruft"$'\n'"$hit"
    done
    if [ -n "$cruft" ]; then
        report 1 hygiene-cruft medium \
            "Backup/output cruft in source or config trees" \
            "Inline backup (.backup_*/.bak) or stray output (.out) files were found (first 10):${cruft}
Remediation: review and remove them (git status will show tracked ones)." \
            86400
    else
        report 0 hygiene-cruft medium "Backup/output cruft in source or config trees" ""
    fi

    # 5b. Backup directory size & retention checks (synchronized with backup lifecycles).
    if [ -d "$BACKUP_DIR" ]; then
        backup_kb=$(du -sk "$BACKUP_DIR" 2>/dev/null | awk '{print $1}')
        backup_kb=${backup_kb:-0}
        backup_max_kb=$(( BACKUP_MAX_GB * 1024 * 1024 ))

        # 5b-1. Check total size against capacity limit
        if [ "$backup_kb" -gt "$backup_max_kb" ]; then
            backups_ok=0
            report 1 hygiene-backups-size medium \
                "Backup directory exceeds size limit" \
                "Backups at $BACKUP_DIR use $(( backup_kb / 1024 )) MB (limit ${BACKUP_MAX_GB} GB). Remediation: remove unneeded backup files or increase BACKUP_MAX_GB limit." \
                86400
        else
            report 0 hygiene-backups-size medium "Backup directory exceeds size limit" ""
        fi

        # 5b-2. FIFO retention check for snapshot & deployment directories
        # Contract: each subfolder (trading-data, services, bots, config, tools) keeps at most 3 backups
        fifo_violations=""
        for sub in trading-data services bots config tools; do
            sub_dir="$BACKUP_DIR/$sub"
            [ -d "$sub_dir" ] || continue
            cnt=$(find "$sub_dir" -maxdepth 1 \( -name 'trading-state-*.tar.gz' -o -name 'backup-*' \) 2>/dev/null | wc -l | tr -d ' ')
            if [ "$cnt" -gt 3 ]; then
                fifo_violations="$fifo_violations"$'\n'"- $sub: found $cnt backups (FIFO limit: 3)"
            fi
        done
        if [ -n "$fifo_violations" ]; then
            backups_ok=0
            report 1 hygiene-backups-fifo medium \
                "FIFO backup retention limit exceeded (> 3)" \
                "The following backup directories exceed the FIFO retention limit (max 3):${fifo_violations}
Remediation: prune older backups in the offending directories (only the newest 3 should be kept)." \
                86400
        else
            report 0 hygiene-backups-fifo medium "FIFO backup retention limit exceeded (> 3)" ""
        fi

        # 5b-3. TTL check for operational dumps (profile switches, manual bot resets)
        # Contract: temporary dumps older than BACKUP_MAX_AGE_DAYS (30d) should be cleaned up
        old_dumps=$(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'trading_profile_backup_*.txt' -o -name 'trader_state_*_backup_*.json' \) \
                     -mtime "+${BACKUP_MAX_AGE_DAYS}" -print 2>/dev/null | head -n 5)
        if [ -n "$old_dumps" ]; then
            backups_ok=0
            report 1 hygiene-backups-dumps medium \
                "Stale operational backup dumps found (> ${BACKUP_MAX_AGE_DAYS} days)" \
                "Operational dumps in $BACKUP_DIR exceed retention of ${BACKUP_MAX_AGE_DAYS} days (first 5):
${old_dumps}
Remediation: remove old profile switch or bot reset files." \
                86400
        else
            report 0 hygiene-backups-dumps medium "Stale operational backup dumps found (> ${BACKUP_MAX_AGE_DAYS} days)" ""
        fi

        # 5b-4. Archive integrity & freshness verification for trading data backups
        # Contract: latest snapshot must be non-empty, pass 'tar -tzf', and not be older than BACKUP_MAX_HOURS (+ weekend tolerance)
        if [ -d "$BACKUP_DIR/trading-data" ]; then
            latest_tar=$(ls -dt "$BACKUP_DIR"/trading-data/trading-state-*.tar.gz 2>/dev/null | head -n 1)
            if [ -z "$latest_tar" ]; then
                backups_ok=0
                report 1 hygiene-backups-freshness high \
                    "No trading data backups found" \
                    "No backup files found in $BACKUP_DIR/trading-data. Remediation: run 'bash scripts/backup-trading-data.sh' to establish initial backup."
                report 0 hygiene-backups-integrity high "Latest trading data backup is corrupted" ""
            else
                # Check archive integrity
                if [ ! -s "$latest_tar" ] || ! tar -tzf "$latest_tar" >/dev/null 2>&1; then
                    backups_ok=0
                    report 1 hygiene-backups-integrity high \
                        "Latest trading data backup is corrupted" \
                        "Latest backup archive '$latest_tar' is empty or failed integrity test (tar -tzf). Remediation: run 'bash scripts/backup-trading-data.sh' to create a fresh valid backup."
                else
                    report 0 hygiene-backups-integrity high "Latest trading data backup is corrupted" ""
                fi

                # Check archive freshness
                tar_mtime=$(file_mtime "$latest_tar")
                if [ -n "$tar_mtime" ]; then
                    tar_age_h=$(( (now_epoch - tar_mtime) / 3600 ))
                    freshness_limit=$(( ${BACKUP_MAX_HOURS:-50} + weekend_extra ))
                    if [ "$tar_age_h" -gt "$freshness_limit" ]; then
                        backups_ok=0
                        report 1 hygiene-backups-freshness high \
                            "Latest trading backup is stale (${tar_age_h}h old)" \
                            "Latest backup '$latest_tar' was created ${tar_age_h}h ago (limit ${freshness_limit}h). The scheduled backup process may have silently failed. Remediation: run 'bash scripts/backup-trading-data.sh' and check systemctl status trading-data-backup.service."
                    else
                        report 0 hygiene-backups-freshness high "Latest trading backup is stale" ""
                    fi
                fi
            fi
        fi

        # Clear legacy alert markers if present from earlier watchdog versions
        report 0 hygiene-backups medium "Backup directory over size/age limits" ""
        report 0 hygiene-backups-age medium "Old backup snapshots found (> ${BACKUP_MAX_AGE_DAYS} days)" ""
    fi

    # 5c. Telemetry log directory hygiene (.ndjson retention: 14 days).
    # Prunes old rotated / compressed ndjson files exceeding retention policy.
    for metrics_dir in $HOME/tradingbots/data/*/execution_metrics $HOME/tradingbots/logs/execution_metrics; do
        [ -d "$metrics_dir" ] || continue
        find "$metrics_dir" -type f \( -name '*.ndjson.*' -o -name '*.ndjson-*.gz' -o -name '*.ndjson.gz' \) \
             -mtime "+${TELEMETRY_RETENTION_DAYS}" -delete 2>/dev/null || true
    done
fi
# backups_ok only reflects a day we actually scanned; on skipped runs keep the
# last published value neutral-positive so the digest is not falsely alarming.
SNAP_BACKUPS_OK=$backups_ok

# Publish the snapshot for the daily digest (best-effort; never fatal). Only
# pass flags we actually measured so unknown fields are stored as null.
snap_args=(--stale-locks "$SNAP_STALE_LOCKS" --missed-runs "$SNAP_MISSED_RUNS" \
           --backups-ok "$SNAP_BACKUPS_OK" --ownership-ok "$SNAP_OWNERSHIP_OK")
[ -n "$SNAP_DISK" ] && snap_args+=(--disk-pct "$SNAP_DISK")
[ -n "$SNAP_SWAP" ] && snap_args+=(--swap-used-mb "$SNAP_SWAP")
if [ -z "$DRY_RUN_FLAG" ]; then
    "$PYTHON_BIN" "$SNAPSHOT_CLI" "${snap_args[@]}" >> "$LOG_FILE" 2>&1 || true
else
    log "dry-run: would publish snapshot ${snap_args[*]}"
fi

log "ops watchdog done: stale_locks=$SNAP_STALE_LOCKS missed_runs=$SNAP_MISSED_RUNS swap=${SNAP_SWAP:-n/a}MB disk=${SNAP_DISK:-n/a}% ownership_ok=$SNAP_OWNERSHIP_OK backups_ok=$SNAP_BACKUPS_OK"
exit 0

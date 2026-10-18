#!/bin/bash
#
# OnFailure= handler for systemd units — see service-failure-notifier@.service.
#
# Usage: systemd-failure-notify.sh <failed-unit-name>
#
# Assembles an actionable message (unit name, systemd's own failure verdict, and
# the tail of the unit's journal) and hands it to services/notify_alert.py, which
# delivers it over SMTP. Repeats for the same unit are throttled to once per hour
# by the CLI, so a crash-looping unit cannot storm the operator's inbox.
#
# This script must never itself fail the notifier unit: the operator already has
# one broken thing to look at. Every diagnostic step is best-effort.
#
# Note: the body is piped straight into the notifier's stdin (--message -) rather
# than assembled in a variable. A heredoc inside "$( ... )" is parsed as shell
# code, and journal text is not under our control.

set -uo pipefail

UNIT="${1:-unknown-unit}"
JOURNAL_TAIL_LINES="${JOURNAL_TAIL_LINES:-25}"
PYTHON_BIN="${PYTHON_BIN:-/home/i030983/venv311/bin/python3}"
NOTIFY_CLI="${NOTIFY_CLI:-/home/i030983/tradingbots/services/notify_alert.py}"

# systemd's verdict: Result is the machine-readable outcome (exit-code / signal /
# timeout / watchdog), ExecMainStatus is the raw exit code or signal number.
describe=$(systemctl show --no-pager --property=Description --value "$UNIT" 2>/dev/null || true)
state=$(systemctl show --no-pager \
    --property=ActiveState,SubState,Result,ExecMainStatus,ExecMainCode \
    "$UNIT" 2>/dev/null || echo "ActiveState=(unavailable)")
journal=$(journalctl -u "$UNIT" -n "$JOURNAL_TAIL_LINES" --no-pager 2>/dev/null || echo "(journal unavailable)")

{
    printf 'systemd unit failed: %s\n' "$UNIT"
    if [ -n "$describe" ]; then
        printf 'Description: %s\n' "$describe"
    fi
    printf '\nsystemd verdict:\n%s\n' "$state"
    printf '\n--- last %s journal lines (journalctl -u %s) ---\n' "$JOURNAL_TAIL_LINES" "$UNIT"
    printf '%s\n' "$journal"
} | "$PYTHON_BIN" "$NOTIFY_CLI" \
    --priority critical \
    --source systemd \
    --title "Service failed: ${UNIT}" \
    --message - \
    --dedup-key "systemd-failure:${UNIT}" \
    --min-interval 3600 || true

# Always succeed: the notification attempt is the whole job. A non-zero exit here
# would only add a second, less useful failed unit to the picture.
exit 0

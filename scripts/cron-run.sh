#!/bin/bash
#
# Cron wrapper that pages the operator when a scheduled job fails.
#
# Usage: cron-run.sh <job-name> <command> [args...]
#
# `cron` on this VM has no mail relay, so a failing nightly job otherwise fails
# completely silently — the failure only ever reaches a log file nobody tails.
# This wrapper runs the command, passes its output through untouched (so the
# existing ">> log 2>&1" redirections in the crontab keep working), and on a
# non-zero exit sends one alert through the notification pipeline.
#
# Example crontab line:
#   */5 * * * * /home/i030983/scripts/cron-run.sh health-check /home/i030983/scripts/health-check.sh
#
# The job's exit status is preserved so cron's own bookkeeping stays truthful.
#
# Note: the alert body is piped straight into the notifier's stdin (--message -)
# rather than assembled in a variable. A heredoc inside "$( ... )" is parsed as
# shell code, so any apostrophe in the text would break the script at parse time.

set -uo pipefail

JOB="${1:?usage: cron-run.sh <job-name> <command> [args...]}"
shift

PYTHON_BIN="${PYTHON_BIN:-/home/i030983/venv311/bin/python3}"
NOTIFY_CLI="${NOTIFY_CLI:-/home/i030983/tradingbots/services/notify_alert.py}"

"$@"
rc=$?

if [ "$rc" -ne 0 ]; then
    {
        printf 'Scheduled job failed: %s\n' "$JOB"
        printf 'Exit code : %s\n' "$rc"
        printf 'Finished  : %s\n' "$(date +%Y-%m-%dT%H:%M:%S%z)"
        printf 'Host      : %s\n' "$(hostname)"
        printf 'Command   : %s\n' "$*"
        printf '\nCheck the job log under $HOME/logs (or the redirection in the\n'
        printf 'crontab) for the captured output.\n'
    } | "$PYTHON_BIN" "$NOTIFY_CLI" \
        --priority high \
        --source cron \
        --title "Cron job failed: ${JOB} (exit ${rc})" \
        --message - \
        --dedup-key "cron-failure:${JOB}" \
        --min-interval 3600 || true
fi

exit "$rc"

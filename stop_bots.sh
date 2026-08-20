#!/bin/bash
#
# Graceful shutdown script for trading bot manager
# Called by systemd when stopping the service
#

set -e

LOG_PREFIX="[SHUTDOWN]"
MANAGER_PID_FILE="/tmp/trading-bot-manager.pid"

echo "$LOG_PREFIX Starting graceful shutdown..."

# Find bot_manager.py process
BOT_MANAGER_PID=$(pgrep -f "python3.*bot_manager.py" || true)

if [ -z "$BOT_MANAGER_PID" ]; then
    echo "$LOG_PREFIX No bot manager process found, nothing to stop"
    exit 0
fi

echo "$LOG_PREFIX Found bot manager PID: $BOT_MANAGER_PID"

# Send SIGTERM for graceful shutdown
echo "$LOG_PREFIX Sending SIGTERM to bot manager..."
kill -TERM "$BOT_MANAGER_PID" 2>/dev/null || true

# Wait for process to exit (max 25 seconds, systemd allows 30)
TIMEOUT=25
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT ]; do
    if ! kill -0 "$BOT_MANAGER_PID" 2>/dev/null; then
        echo "$LOG_PREFIX Bot manager stopped gracefully after ${ELAPSED}s"
        exit 0
    fi

    sleep 1
    ELAPSED=$((ELAPSED + 1))

    if [ $((ELAPSED % 5)) -eq 0 ]; then
        echo "$LOG_PREFIX Waiting for graceful shutdown... ${ELAPSED}s/${TIMEOUT}s"
    fi
done

# Force kill if still running (shouldn't happen with Restart=no)
if kill -0 "$BOT_MANAGER_PID" 2>/dev/null; then
    echo "$LOG_PREFIX WARNING: Graceful shutdown timed out, force killing..."
    kill -9 "$BOT_MANAGER_PID" 2>/dev/null || true
    exit 1
fi

exit 0

#!/bin/bash
# Health check script for trading bot system

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
ALERT_FILE="$LOG_DIR/health-alerts.log"
LOG_FILE="$LOG_DIR/health-check.log"

alert() {
    echo "[$(date)] ALERT: $1" | tee -a "$ALERT_FILE"
}

# 1. Check Docker & IB Gateway container
if ! docker ps > /dev/null 2>&1; then
    alert "Docker daemon is not running!"
    exit 1
fi

if ! docker ps | grep -q ib-gateway; then
    alert "IB Gateway container is not running!"
    exit 1
fi

# 2. Check if IB Gateway port 4002 is listening
if ! sudo ss -tlnp | grep -q ":4002"; then
    alert "IB Gateway API port 4002 is not listening!"
fi

# 3. Check Core Background Services
if ! systemctl is-active --quiet bot-control-api.service; then
    alert "bot-control-api service is not active!"
fi

if ! systemctl is-active --quiet notification-service.service; then
    alert "notification-service is not active!"
fi

# 4. Check Disk space (alert if > 80% full)
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    alert "Disk usage is at ${DISK_USAGE}%"
fi

# 5. Check Memory usage (alert if > 90% full)
MEM_USAGE=$(free | grep Mem | awk '{print int($3/$2 * 100)}')
if [ "$MEM_USAGE" -gt 90 ]; then
    alert "Memory usage is at ${MEM_USAGE}%"
fi

echo "[$(date)] Health check passed" >> "$LOG_FILE"

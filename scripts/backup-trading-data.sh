#!/bin/bash
# Backup trading bot state files and configuration

BACKUP_DIR="$HOME/backups/trading-data"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/trading-state-$DATE.tar.gz"

# Create backup of active trading engine state and configs
tar -czf "$BACKUP_FILE" \
    -C /home/i030983 \
    tradingbots/data \
    tradingbot-config \
    .ibgateway.env 2>/dev/null

echo "Backup created: $BACKUP_FILE"

# Enforce strict maximum of 3 backups (FIFO: delete oldest when count > 3)
ls -dt "$BACKUP_DIR"/trading-state-*.tar.gz 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null || true

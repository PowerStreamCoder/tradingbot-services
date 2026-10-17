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

# Keep only last 14 days of backups (delete older)
find "$BACKUP_DIR" -name "trading-state-*.tar.gz" -mtime +14 -delete

echo "Backup created: $BACKUP_FILE"

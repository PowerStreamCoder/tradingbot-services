#!/bin/bash
# Backup trading bot state files and configuration
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/trading-data}"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/trading-state-$DATE.tar.gz"
TMP_FILE="$BACKUP_FILE.tmp.$$"

# Clean up temp file on unexpected exit
trap 'rm -f "$TMP_FILE" 2>/dev/null || true' EXIT

SRC_DIR="${HOME:-/home/i030983}"
ITEMS=()
for item in tradingbots/data tradingbot-config .ibgateway.env; do
    if [ -e "$SRC_DIR/$item" ]; then
        ITEMS+=("$item")
    fi
done

if [ ${#ITEMS[@]} -eq 0 ]; then
    echo "ERROR: No trading state files or directories found in $SRC_DIR to back up." >&2
    exit 1
fi

# 1. Create backup archive to temporary file
echo "Creating backup from $SRC_DIR: ${ITEMS[*]}..."
tar_rc=0
tar_err=$(tar -czf "$TMP_FILE" -C "$SRC_DIR" "${ITEMS[@]}" 2>&1) || tar_rc=$?
# In GNU tar, exit code 1 indicates non-fatal file changes during read; >1 indicates fatal error
if [ "$tar_rc" -gt 1 ]; then
    echo "ERROR: tar fatal error during backup creation (exit code $tar_rc): $tar_err" >&2
    exit 1
fi

# 2. Verify archive exists and is non-empty
if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: Backup file was created empty (0 bytes)." >&2
    exit 1
fi

# 3. Verify archive integrity (headers and gzip blocks)
if ! tar -tzf "$TMP_FILE" >/dev/null 2>&1; then
    echo "ERROR: Backup archive failed integrity test (tar -tzf)." >&2
    exit 1
fi

# 4. Atomically commit the verified backup
mv "$TMP_FILE" "$BACKUP_FILE"
echo "Backup created and verified: $BACKUP_FILE"

# 5. Enforce strict maximum of 3 backups (FIFO: delete oldest only AFTER new backup is verified)
ls -dt "$BACKUP_DIR"/trading-state-*.tar.gz 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null || true

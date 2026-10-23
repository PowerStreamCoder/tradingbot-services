# Trading Bot Services (`tradingbot-services`)

Systemd service definitions, market timer configurations, failure alerting units, and operational automation scripts for automated GCP VM operations.

---

## 🛠️ Overview

This repository manages the OS-level orchestration of trading bots running on the GCP Compute Engine VM:
- **Fleet Supervision**: `trading-bot-manager.service` runs the bot manager daemon (source maintained in `tradingbot-bots/services/bot_manager.py`) as unprivileged user `i030983`, supervising child bot processes for all enabled symbols in `bots.json`.
- **Market Schedule Timers**: Systemd timers automatically start bot processes prior to market open (8:51 AM ET) and execute graceful shutdown procedures after market close (4:39 PM ET).
- **Graceful Shutdown (`stop_bots.sh`)**: Sends orderly SIGTERM signals, providing bots a 25-second window to persist open bucket states and flush logs before hard termination.
- **Bot Control API Unit (`bot-control-api.service`)**: Runs the authenticated REST command server (`:8080`) enabling remote actions from the Cloud Run Dashboard. Configured with dedicated `WorkingDirectory`, `PYTHONPATH`, and drop-in `override.conf` for `BOT_CONTROL_API_KEY`.
- **Trade Log Auditor Unit (`trade-log-auditor.service` / `.timer`)**: Runs the offline post-close trade-log auditor at 4:45 PM ET Mon-Fri with `--date latest`, verifying trade integrity before nightly learning runs.
- **Nightly Learning Analysis Unit (`learning-analysis.service` / `.timer`)**: Runs post-market learning analysis at 11:00 PM ET.
- **24/7 Notification Watchdog (`notification-service.service`)**: Standalone watchdog monitoring Firestore for hung bots, daily loss thresholds, and option assignment risks.
- **Automated Failure Alerting (`service-failure-notifier@.service`)**: Systemd failure hook that alerts operators immediately via email whenever any monitored service, timer, or cron job fails (#25).
- **Operational Watchdog (`ops-watchdog.service` / `.timer`)**: Runs `scripts/ops-watchdog.sh` every 15 minutes to catch the *silent* failure modes a VM audit surfaced (#310): stale lock files (#98), missed scheduled runs, swap saturation, data/config ownership drift (#297), and filesystem-hygiene drift. Alerts flow through the same `notify_alert.py` pipeline, and a compact health snapshot is published to Firestore (`system_health/current`) for the daily digest.
- **Operational Health & Backup Scripts**: Automated VM diagnostics (`scripts/health-check.sh`) and database backup retention (`scripts/backup-trading-data.sh`).

---

## 📁 Repository Structure

```
tradingbot-services/
├── trading-bot-manager.service            # Main systemd service supervising bot fleet
├── trading-bot-manager-start.timer        # Timer auto-starting bots at 8:51 AM ET Mon-Fri
├── trading-bot-manager-stop.timer         # Timer auto-stopping bots at 4:39 PM ET Mon-Fri
├── trading-bot-manager-stop.service       # Service executing stop_bots.sh
├── bot-control-api.service                # Authenticated REST API server for dashboard controls
├── trade-log-auditor.service              # Post-close trade-log audit (oneshot, read-only)
├── trade-log-auditor.timer                # Schedules audit at 4:45 PM ET Mon-Fri
├── learning-analysis.service              # Nightly learning analysis unit
├── learning-analysis.timer                # Schedules learning analysis at 11:00 PM ET
├── notification-service.service           # 24/7 background Firestore notification watchdog
├── service-failure-notifier@.service      # Dynamic systemd OnFailure notification unit
├── ops-watchdog.service                   # Operational watchdog (oneshot): locks, missed runs, swap, ownership, hygiene
├── ops-watchdog.timer                     # Schedules the operational watchdog every 15 minutes
├── tradingbots.logrotate                  # Standard daily logrotate policy (14-day retention, compress, copytruncate)
├── stop_bots.sh                           # Orderly shutdown and state preservation script
├── scripts/                               # Operational diagnostics and automation scripts
│   ├── health-check.sh                    # Automated system, socket, and memory health check
│   ├── ops-watchdog.sh                    # Stale-lock / missed-run / swap / ownership / hygiene watchdog (#310)
│   ├── backup-trading-data.sh             # Operational backup script for trading data
│   ├── systemd-failure-notify.sh          # Dispatches alerts when systemd units fail
│   ├── cron-run.sh                        # Wrapper for cron job execution with error capture
│   └── setup-git-hooks.sh                 # Pre-push hook preventing direct push to main
└── .github/workflows/
    ├── deploy-services.yml                # Protected manual deployment workflow (with FIFO backups)
    └── pr-checks.yml                      # Syntax validation workflow
```

---

## ⚙️ Service Lifecycle & Commands

### 1. Managing the Bot Fleet
```bash
# Check status of the bot manager and child bots
sudo systemctl status trading-bot-manager

# Follow live multi-bot stdout/stderr logs
sudo journalctl -u trading-bot-manager -f

# Manual start / stop / restart
sudo systemctl start trading-bot-manager
sudo systemctl stop trading-bot-manager
sudo systemctl restart trading-bot-manager
```

### 2. Inspecting Configured Timers
```bash
# List all active market open / close timers and next scheduled trigger
sudo systemctl list-timers trading-bot-manager*

# List all systemd timers across all services
sudo systemctl list-timers
```

### 3. Trade Log Auditor
```bash
# Check audit status and recent runs
sudo systemctl status trade-log-auditor
sudo journalctl -u trade-log-auditor -n 100 --no-pager

# Run an audit manually
cd /home/i030983/tradingbot-tools
python3 -m auditor --date latest --dry-run
```

### 4. Bot Control API & Authentication
```bash
# Check API server status
sudo systemctl status bot-control-api

# View API logs
sudo journalctl -u bot-control-api -f

# Verify API authentication locally
curl -H "X-API-Key: $BOT_CONTROL_API_KEY" http://localhost:8080/health
```

### 5. Running Health Checks & Backups
```bash
# Run system and network health check
bash scripts/health-check.sh

# Run manual trading data backup
bash scripts/backup-trading-data.sh
```

---

## 🖥️ Remote GUI Access via VNC

VNC port `5900` is blocked from public internet ingress for security. Access to the IB Gateway GUI requires authenticated SSH port forwarding:

```bash
# Establish secure SSH tunnel to the VM
gcloud compute ssh trading-bot-vm --zone=us-central1-a -- -L 5900:localhost:5900

# Open your VNC client and connect to:
# localhost:5900 (Password managed in secure VM vault)
```

---

## 🚢 Service Deployment & Backups

Because service definitions require elevated `sudo` privileges and systemd daemon reloads on the VM, deployment is handled with strict controls:
1. **Protected GitHub Action**: Triggered via `workflow_dispatch` with an explicit `confirm: "deploy"` verification parameter.
2. **Automated VM Steps**: Copies `.service` and `.timer` files to `/etc/systemd/system/`, runs `systemctl daemon-reload`, and validates service active state.
3. **Automated Rollback Backups**: VM backups stored in `~/backups/services` with max 3 FIFO retention.

---

## 🔗 Related Ecosystem Repositories

- [tradingbot-bots](file:///Users/i030983/Library/CloudStorage/GoogleDrive-sudhirmakam@gmail.com/My%20Drive/TradingBots/tradingbot-bots/README.md): Bot application code and `bot_manager.py` runtime source.
- [tradingbot-config](file:///Users/i030983/Library/CloudStorage/GoogleDrive-sudhirmakam@gmail.com/My%20Drive/TradingBots/tradingbot-config/README.md): Registry (`bots.json`) defining enabled symbols.
- [tradingbot-dashboard](file:///Users/i030983/Library/CloudStorage/GoogleDrive-sudhirmakam@gmail.com/My%20Drive/TradingBots/tradingbot-dashboard/README.md): Communicates with `bot-control-api.service`.
- [tradingbot-documentation](file:///Users/i030983/Library/CloudStorage/GoogleDrive-sudhirmakam@gmail.com/My%20Drive/TradingBots/tradingbot-documentation/README.md): VM layout, operations runbooks, and disaster recovery.

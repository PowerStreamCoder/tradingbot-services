# Trading Bot Services (`tradingbot-services`)

Systemd service definitions, market timer configurations, and the `bot_manager` supervisor daemon for automated VM operations.

---

## 🛠️ Overview

This repository manages the OS-level orchestration of trading bots running on the GCP Compute Engine VM:
- **`bot_manager.py` Daemon**: Reads `tradingbot-config/bots.json` and spawns/manages individual `UniversalSMABot.py` processes for all enabled symbols with proper environment variables and client IDs.
- **Market Schedule Timers**: Systemd timers automatically start bot processes prior to market open (8:51 AM ET) and execute graceful shutdown procedures after market close (4:39 PM ET).
- **Graceful Shutdown (`stop_bots.sh`)**: Sends orderly SIGTERM signals, providing bots a 30-second window to persist open bucket states and flush logs before hard termination.
- **Bot Control API Unit (`bot-control-api.service`)**: Runs the standalone REST command server enabling remote actions from the Cloud Run Dashboard.
- **Trade Log Auditor Unit (`trade-log-auditor.service` / `.timer`)**: Runs the offline post-close trade-log auditor at 4:45 PM ET Mon-Fri, after the stop timer and before the 11:00 PM learning analysis, so an audit failure is visible the same evening.

---

## 📁 Repository Structure

```
tradingbot-services/
├── bot_manager.py                          # Bot process supervisor and status monitor
├── trading-bot-manager.service            # Main systemd service running bot_manager.py
├── trading-bot-manager-start.timer        # Systemd timer auto-starting bots at market open
├── trading-bot-manager-stop.timer         # Systemd timer auto-stopping bots at market close
├── trading-bot-manager-stop.service       # Service unit executing stop_bots.sh
├── bot-control-api.service                # Systemd service for dashboard remote command API
├── trade-log-auditor.service              # Post-close trade-log audit (oneshot, read-only)
├── trade-log-auditor.timer                # Schedules the audit at 4:45 PM ET Mon-Fri
├── stop_bots.sh                           # Orderly shutdown and state preservation script
└── .github/workflows/
    ├── deploy-services.yml                # Protected manual deployment workflow
    └── pr-checks.yml                      # Syntax validation workflow
```

---

## ⚙️ Service Lifecycle & Commands

### Managing the Bot Manager
```bash
# Check status of the bot manager and child bots
sudo systemctl status trading-bot-manager

# Follow live multi-bot stdout/stderr logs
sudo journalctl -u trading-bot-manager -f

# Manual start / stop (temporarily overrides timer)
sudo systemctl start trading-bot-manager
sudo systemctl stop trading-bot-manager
```

### Inspecting Configured Timers
```bash
# List all active market open / close timers and next scheduled trigger
sudo systemctl list-timers trading-bot-manager*
```

### Trade Log Auditor
```bash
# When the audit last ran, whether it succeeded, and what it found
sudo systemctl status trade-log-auditor
sudo journalctl -u trade-log-auditor -n 200 --no-pager

# Next scheduled run
systemctl list-timers trade-log-auditor*

# Re-run a session by hand (does not disturb the timer schedule)
cd /home/i030983/tradingbot-tools
python3 -m auditor --date yesterday --no-firestore        # offline, writes nothing
python3 -m auditor --date yesterday --dry-run             # preview only
```

> **Where the auditor code comes from:** the auditor lives in the `tradingbot-tools`
> repository and reaches the VM through that repo's own `deploy-tools.yml`
> (23:45 ET nightly, plus `workflow_dispatch` and a `tools-interface-changed` dispatch
> from `tradingbot-bots`). This repo's deploy workflow copies only units, so it does
> not deliver the code — but it is scheduled at 23:55 ET, 10 minutes *after* the tools
> deploy, so the package is always on the VM before the 16:45 ET audit.
>
> The deploy job enables `trade-log-auditor.timer` only when
> `/home/i030983/tradingbot-tools/auditor` exists, and the unit's `ExecStartPre`
> guards fail with a clear message instead of an import error. The guard is
> belt-and-braces rather than an expected delay: the 10-minute gap is deliberate, so
> the timer comes up on the very first night. If the auditor is not auditing, confirm
> the code landed (`ls /home/i030983/tradingbot-tools/auditor`) and that the timer is
> enabled (`systemctl is-enabled trade-log-auditor.timer`) before investigating the
> unit. See
> [TRADE_LOG_AUDITOR_DESIGN.md](../tradingbot-documentation/designDocs/TRADE_LOG_AUDITOR_DESIGN.md).

### Direct CLI Bot Inspection
```bash
# Check running bot processes and PIDs
python3 bot_manager.py --status

# List all enabled bots configured in bots.json
python3 bot_manager.py --list
```

---

## 🚢 Service Deployment

Because service definitions require elevated `sudo` privileges and systemd daemon reloads on the VM, deployment is handled with strict controls:
1. **Protected GitHub Action**: Triggered via `workflow_dispatch` with an explicit `confirm: "deploy"` verification parameter.
2. **Automated VM Steps**: Copies `.service` and `.timer` files to `/etc/systemd/system/`, runs `systemctl daemon-reload`, and validates service active state.

---

## 🔗 Related Ecosystem Repositories

- [tradingbot-bots](https://github.com/PowerStreamCoder/tradingbot-bots): The runtime bot application supervised by `bot_manager`.
- [tradingbot-config](https://github.com/PowerStreamCoder/tradingbot-config): Registry defining which bots are enabled.
- [tradingbot-dashboard](https://github.com/PowerStreamCoder/tradingbot-dashboard): Communicates with `bot-control-api.service`.
- [tradingbot-documentation](https://github.com/PowerStreamCoder/tradingbot-documentation): VM layout, operations runbooks, and disaster recovery.

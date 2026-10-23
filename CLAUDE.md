<!-- Auto-generated from AGENT_CONTEXT.md — do not edit manually -->
# Services — AI Agent Context

## Main entry points
This repo contains **only systemd service/timer unit files and shell scripts** — no Python code.
All Python code (including the bot manager) lives in `tradingbot-bots/`.

- `trading-bot-manager.service` — Main bot manager service
- `trading-bot-manager-start.timer` — Starts bots at 8:51 AM ET Mon-Fri
- `trading-bot-manager-stop.timer` — Stops bots at 4:39 PM ET Mon-Fri
- `trading-bot-manager-stop.service` — One-shot stop trigger
- `bot-control-api.service` — Dashboard command API (always-running, `Restart=always`)
- `learning-analysis.service` + `.timer` — Nightly ML analysis at 11:00 PM ET Mon-Fri
- `ops-watchdog.service` + `.timer` — Operational watchdog every 15 min (`scripts/ops-watchdog.sh`): stale locks, missed scheduled runs, swap, data/config ownership, filesystem hygiene (#310). Emits via `tradingbot-bots/services/notify_alert.py` and publishes `system_health/current` to Firestore for the daily digest.
- `stop_bots.sh` — Graceful shutdown script (30s timeout for position cleanup)

## Important commands
- Deploy (install/update service files on VM): copy files to `/etc/systemd/system/` then `systemctl daemon-reload`
- **Do NOT trigger the VM deploy manually.** The services deploy reloads systemd and restarts `trading-bot-manager`, which bounces the whole trading fleet. Commit + push and let the **daily scheduled deploy** deliver it. A manual deploy is allowed only when it is **urgent and mandatory** (a live incident or a broken VM state that cannot wait for the schedule) — say why before triggering it, and prefer the pre-market window (the fleet is stopped 16:39 ET → 08:51 ET).
- Check service status: `sudo systemctl status trading-bot-manager`
- **Check the fleet on the VM from the workstation (read-only, no deploy needed):**
  `IP=$(gcloud compute instances list --filter=name=trading-bot-vm --format='value(networkInterfaces[0].accessConfigs[0].natIP)')` then
  `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'systemctl is-active trading-bot-manager; ps -eo cmd | grep "[l]auncher.py" | wc -l; systemctl list-timers --all | grep trading-bot-manager'`.
  The VM's external IP is **not fixed** — always re-resolve it with `gcloud compute instances list` (instance `trading-bot-vm`, zone `us-central1-a`); a stale IP times out and is mistaken for an unreachable VM. Off-hours the manager unit is `active` with **0 bots running** — that is the healthy state; the fleet trades 08:51 → 16:39 ET.
- Restart bots: `sudo systemctl restart trading-bot-manager`
- Check bot control API: `sudo systemctl status bot-control-api`
- View logs: `journalctl -u trading-bot-manager -f`

## Common mappings
- Bot manager process: `trading-bot-manager.service` → runs `tradingbot-bots/services/bot_manager.py`
- Dashboard command API: `bot-control-api.service` → runs `tradingbot-bots/trading/bot_control_api.py`
- Auto-start schedule: `trading-bot-manager-start.timer` (8:51 AM ET) + `trading-bot-manager-stop.timer` (4:39 PM ET)
- Trading profile env var: sourced from `/home/i030983/.trading_profile` at service start
- Bot code deploy path on VM: `/home/i030983/tradingbots`

## Debugging rules
- If bots didn't start at market open: check `systemctl status trading-bot-manager-start.timer` and `journalctl -u trading-bot-manager`
- If bot-control-api is crashing (CHDIR loop): verify `WorkingDirectory` in `bot-control-api.service` points to the correct path; this was a known bug
- If changes to service files have no effect: run `systemctl daemon-reload` after copying to `/etc/systemd/system/`
- If a scheduled job silently stopped or a `/tmp/*.lock` went stale (#98/#310): check `ops-watchdog.service` (`journalctl -u ops-watchdog`, `~/logs/ops-watchdog.log`); the watchdog clears stale locks it reports via the remediation line in its alert, and `~/logs/health-state/` holds its fault markers.
- Never read the entire repository unless explicitly required

## Architecture notes
- This repo is infrastructure-only — Python logic is in `tradingbot-bots/`, config is in `tradingbot-config/`
- `trading-bot-manager.service` has `Restart=no` — lifecycle is controlled by the timers, not auto-restart
- `bot-control-api.service` has `Restart=always, RestartSec=10` — it should always be running
- `stop_bots.sh` gives a 30-second window for bots to close open positions before hard kill
- `learning-analysis.service` has memory (1 GB) and CPU (50%) limits to avoid VM resource exhaustion

## Task recipes

### Deploy updated service files to VM
1. `scp <file>.service user@vm:/etc/systemd/system/`
2. `ssh user@vm sudo systemctl daemon-reload`
3. `ssh user@vm sudo systemctl restart <service-name>`

### Change bot start/stop time
Edit `OnCalendar=` in `trading-bot-manager-start.timer` or `trading-bot-manager-stop.timer`.
Format: `Mon-Fri *-*-* HH:MM:SS America/New_York`

### VNC Desktop Access (IB Gateway GUI)
Port 5900 is restricted to localhost (blocked on all external network interfaces).
To access the IB Gateway GUI:
1. Open an SSH tunnel:
   `gcloud compute ssh trading-bot-vm --zone=us-central1-a -- -L 5900:localhost:5900`
2. Connect your local VNC viewer to `vnc://localhost:5900`

## Documentation
- System overview: `../tradingbot-documentation/guides/SYSTEM_OVERVIEW.md`
- VM organization: `../tradingbot-documentation/architecture/VM_ORGANIZATION.md`
- Services README: `../tradingbot-documentation/services/README.md`
- Deployment guide: `../tradingbot-documentation/deployment/README_DEPLOY.md`
- Operator runbook: `../tradingbot-documentation/guides/OPERATOR_RUNBOOK.md`
- Fixes & incident history: `../tradingbot-documentation/reviews/FIXES_LOG.md`

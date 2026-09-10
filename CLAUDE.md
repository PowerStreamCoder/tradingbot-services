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
- `stop_bots.sh` — Graceful shutdown script (30s timeout for position cleanup)

## Important commands
- Deploy (install/update service files on VM): copy files to `/etc/systemd/system/` then `systemctl daemon-reload`
- Check service status: `sudo systemctl status trading-bot-manager`
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

## Existing docs
- VM organization: `../tradingbot-documentation/architecture/VM_ORGANIZATION.md`
- Deployment guide: `../tradingbot-documentation/deployment/`

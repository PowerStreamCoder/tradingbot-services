# Trading Bot Services

Systemd service files and infrastructure configuration for trading bot deployment.

## Overview

This repository contains systemd service files, timers, and the bot manager entry point that systemd executes.

## Structure

```
tradingbot-services/
├── bot_manager.py                          # Entry point called by systemd
├── trading-bot-manager.service            # Main service definition
├── trading-bot-manager-start.timer        # Auto-start timer
├── trading-bot-manager-stop.timer         # Auto-stop timer
├── trading-bot-manager-stop.service       # Stop service
├── bot-control-api.service                # Dashboard API control service
├── stop_bots.sh                           # Graceful shutdown script
└── README.md
```

## Files

### bot_manager.py
Python script that systemd executes. This is the entry point for the bot system.

### Systemd Service Files
- `trading-bot-manager.service` - Main bot service (started/stopped by timers)
- `bot-control-api.service` - Dashboard command API service
- `trading-bot-manager-start.timer` - Auto-starts bots at market open
- `trading-bot-manager-stop.timer` - Auto-stops bots at market close
- `trading-bot-manager-stop.service` - Stop service

### Scripts
- `stop_bots.sh` - Graceful shutdown (gives bots 30s to close positions)

## Deployment

Service changes require manual installation on the VM:

```bash
# On local machine
cd tradingbot-services
git pull

# Copy to VM
scp * trading-bot-vm:/home/i030983/tradingbots/services/

# Install to systemd
ssh trading-bot-vm
cd /home/i030983/tradingbots/services
sudo cp *.service *.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl status trading-bot-manager
```

## Change Frequency

⚠️ **Very low frequency** - Service changes are rare (quarterly or less).

Common changes:
- Adjusting market hours timers
- Modifying restart policies
- Adding new services

## Systemd Commands

```bash
# View service status
sudo systemctl status trading-bot-manager

# View logs
sudo journalctl -u trading-bot-manager -f

# Manual start/stop (overrides timers)
sudo systemctl start trading-bot-manager
sudo systemctl stop trading-bot-manager

# View timer status
sudo systemctl list-timers trading-bot-manager*
```

## Runtime Overrides in Production

Any bot parameter can be changed in production **without redeploying code** using environment variables. This is the recommended approach for emergency hotfixes or quick experiments.

### How It Works

Environment variables with the `TRADING_` prefix override any configuration tier at startup:

```
TRADING_<PARAM_NAME_UPPERCASE>=<value>
```

Type conversion is automatic:
- `TRADING_USE_COVERED_CALLS=false` → bool `False`
- `TRADING_ATR_PERIOD=20` → int `20`
- `TRADING_STOP_LOSS_THRESHOLD=0.97` → float `0.97`
- `TRADING_SYMBOL=IWM` → str `"IWM"`

### Persistent Overrides via `.trading_profile`

For overrides that should survive service restarts, add them to `/home/i030983/.trading_profile` on the VM. The service sources this file on startup.

```bash
ssh trading-bot-vm

# Add override (appends — does not replace existing entries)
echo "TRADING_STOP_LOSS_THRESHOLD=0.97" >> /home/i030983/.trading_profile

# Or edit directly for multiple changes
nano /home/i030983/.trading_profile
```

Restart the service to apply:

```bash
sudo systemctl restart trading-bot-manager

# Confirm override was applied — look for [CONFIG] lines
sudo journalctl -u trading-bot-manager -n 50 | grep "\[CONFIG\]"
```

### Common Production Hotfixes

**Tighten stop loss temporarily (e.g. volatile market):**
```bash
echo "TRADING_STOP_LOSS_THRESHOLD=0.97" >> /home/i030983/.trading_profile
sudo systemctl restart trading-bot-manager
```

**Disable covered calls for a specific session:**
```bash
echo "TRADING_USE_COVERED_CALLS=false" >> /home/i030983/.trading_profile
sudo systemctl restart trading-bot-manager
```

**Reduce capital allocation while testing:**
```bash
echo "TRADING_CAPITAL_PER_BUCKET_LONG=10000" >> /home/i030983/.trading_profile
sudo systemctl restart trading-bot-manager
```

**Verify overrides are active:**
```bash
sudo journalctl -u trading-bot-manager | grep "Environment overrides applied"
# Expected: [CONFIG] Environment overrides applied: stop_loss_threshold=0.97
```

**Remove an override:**
```bash
# Edit the file and delete the relevant line
nano /home/i030983/.trading_profile
sudo systemctl restart trading-bot-manager
```

### One-Time Override (Testing Only)

For a single run without persisting to `.trading_profile`:

```bash
sudo systemctl stop trading-bot-manager

# Run manually with override
TRADING_CAPITAL_PER_BUCKET_LONG=5000 \
TRADING_PROFILE=paper \
python /home/i030983/tradingbots/bots/UniversalSMABot.py

# When satisfied, restart the service normally
sudo systemctl start trading-bot-manager
```

### Override Priority Reference

```
TRADING_* env vars           (highest — always wins)
    ↓ overrides
bots/<symbol>.json           (per-symbol tuning)
    ↓ overrides
UniversalSMABot strategy_overrides  (strategy logic defaults)
    ↓ overrides
profiles/paper.json or live.json    (environment settings)
```

See [tradingbot-config/bots/README.md](https://github.com/PowerStreamCoder/tradingbot-config/blob/main/bots/README.md) for the full list of configurable parameters.

## Related Repositories

- [tradingbot-bots](https://github.com/PowerStreamCoder/tradingbot-bots) - Bot runtime code
- [tradingbot-config](https://github.com/PowerStreamCoder/tradingbot-config) - Configuration files
- [tradingbot-secrets](https://github.com/PowerStreamCoder/tradingbot-secrets) - Secrets (private)

## License

Private repository - All rights reserved

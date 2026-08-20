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

## Related Repositories

- [tradingbot-bots](https://github.com/PowerStreamCoder/tradingbot-bots) - Bot runtime code
- [tradingbot-config](https://github.com/PowerStreamCoder/tradingbot-config) - Configuration files
- [tradingbot-secrets](https://github.com/PowerStreamCoder/tradingbot-secrets) - Secrets (private)

## License

Private repository - All rights reserved

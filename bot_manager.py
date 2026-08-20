#!/usr/bin/env python3
"""
Universal Trading Bot Manager (v3.0)

A single systemd service that manages all trading bots dynamically.
No need to create separate service files for each bot.

Features:
- Dynamic bot discovery from config file
- Start/stop individual bots or all bots
- Auto-restart on crash
- Centralized logging
- Health monitoring
- Graceful shutdown

Usage:
    # Start all enabled bots
    python3 bot_manager.py

    # Start specific bots
    python3 bot_manager.py --bots nvda msft

    # List available bots
    python3 bot_manager.py --list

    # Status check
    python3 bot_manager.py --status

Configuration:
    Edit config/bots.json to add/remove/configure bots
"""

import os
import sys
import json
import signal
import subprocess
import time
import logging
import argparse
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot_manager.log')
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class BotConfig:
    """Configuration for a single bot."""
    name: str
    symbol: str
    client_id: int
    script: str
    enabled: bool
    description: str
    strategy: Optional[str] = None  # Optional strategy selection (e.g., "momentum", "sma_crossover")
    extra_args: Optional[List[str]] = None  # Additional command-line arguments
    capital_override: Optional[float] = None  # Per-symbol capital allocation override

    @property
    def display_name(self) -> str:
        return f"{self.symbol} ({self.name})"


@dataclass
class BotProcess:
    """Running bot process information."""
    config: BotConfig
    process: subprocess.Popen
    start_time: datetime
    restart_count: int = 0
    last_restart: Optional[datetime] = None


class BotManager:
    """Manages multiple trading bot processes."""

    def __init__(self, config_file: str = "config/bots.json"):
        self.config_file = config_file
        self.bots: Dict[str, BotConfig] = {}
        self.processes: Dict[str, BotProcess] = {}
        self.running = False
        self.defaults = {}

        # Load configuration
        self._load_config()

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self) -> None:
        """Load bot configuration from JSON file."""
        try:
            with open(self.config_file) as f:
                config = json.load(f)

            self.defaults = config.get('defaults', {})

            for bot_data in config.get('bots', []):
                bot = BotConfig(**bot_data)
                self.bots[bot.name] = bot
                logger.info(f"Loaded config for {bot.display_name}: {bot.description}")

            # Validate bot configurations
            self._validate_bot_configs()

            logger.info(f"Loaded {len(self.bots)} bot configurations")

        except Exception as e:
            logger.error(f"Failed to load config from {self.config_file}: {e}")
            sys.exit(1)

    def _validate_bot_configs(self) -> None:
        """
        Validate bot configurations for common errors.

        Checks:
        - Duplicate symbols among enabled bots (prevents Firestore overwrites)
        - Duplicate client IDs among enabled bots (prevents IBKR connection conflicts)
        - Empty or missing symbols
        - Invalid client IDs

        Raises:
            ValueError: If validation fails
        """
        enabled_bots = [bot for bot in self.bots.values() if bot.enabled]

        if not enabled_bots:
            return  # No validation needed if no bots enabled

        # Validate symbols exist and are non-empty
        for bot in enabled_bots:
            if not bot.symbol or not bot.symbol.strip():
                error_msg = f"Bot '{bot.name}' has empty or missing symbol"
                logger.error(error_msg)
                raise ValueError(error_msg)

            if not isinstance(bot.client_id, int) or bot.client_id < 1:
                error_msg = f"Bot '{bot.name}' has invalid client_id: {bot.client_id}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        # Check for duplicate symbols (case-insensitive)
        from collections import Counter
        symbols = [bot.symbol.upper() for bot in enabled_bots]
        symbol_counts = Counter(symbols)
        duplicates = [s for s, count in symbol_counts.items() if count > 1]

        if duplicates:
            error_msg = (
                f"Duplicate symbols detected among enabled bots: {duplicates}\n"
                f"Multiple bots trading the same symbol will overwrite each other's "
                f"Firestore documents. Each enabled bot must have a unique symbol."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Check for duplicate client IDs
        client_ids = [bot.client_id for bot in enabled_bots]
        client_id_counts = Counter(client_ids)
        duplicates = [cid for cid, count in client_id_counts.items() if count > 1]

        if duplicates:
            error_msg = (
                f"Duplicate client IDs detected among enabled bots: {duplicates}\n"
                f"Multiple bots with the same client_id will conflict when connecting "
                f"to IBKR. Each enabled bot must have a unique client_id."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _start_bot(self, bot_config: BotConfig) -> Optional[BotProcess]:
        """Start a single bot process."""
        if not bot_config.enabled:
            logger.warning(f"{bot_config.display_name} is disabled in config, skipping")
            return None

        try:
            script_path = os.path.join(
                self.defaults.get('working_directory', os.getcwd()),
                bot_config.script
            )

            if not os.path.exists(script_path):
                logger.error(f"Bot script not found: {script_path}")
                return None

            python_path = self.defaults.get('python_path', sys.executable)

            # Build command with optional strategy and extra args
            cmd = [python_path, '-u', script_path]

            # Build environment with strategy and symbol as env vars
            env = os.environ.copy()

            # Pass symbol to bot script (REQUIRED for symbol-agnostic bots)
            env['TRADING_SYMBOL'] = bot_config.symbol

            # Pass strategy if specified
            if bot_config.strategy:
                env['STRATEGY'] = bot_config.strategy

            # Add any extra arguments
            if bot_config.extra_args:
                cmd.extend(bot_config.extra_args)

            logger.info(f"Starting {bot_config.display_name}...")
            logger.info(f"  Script: {script_path}")
            logger.info(f"  Symbol: {bot_config.symbol}")
            logger.info(f"  Client ID: {bot_config.client_id}")
            if bot_config.strategy:
                logger.info(f"  Strategy: {bot_config.strategy}")

            process = subprocess.Popen(
                cmd,
                stdout=None,
                stderr=None,
                bufsize=1,
                universal_newlines=True,
                env=env
            )

            bot_process = BotProcess(
                config=bot_config,
                process=process,
                start_time=datetime.now()
            )

            logger.info(f"✅ Started {bot_config.display_name} (PID: {process.pid})")
            return bot_process

        except Exception as e:
            logger.error(f"Failed to start {bot_config.display_name}: {e}")
            return None

    def _stop_bot(self, bot_name: str, timeout: int = 10) -> bool:
        """Stop a single bot process."""
        if bot_name not in self.processes:
            logger.warning(f"Bot {bot_name} is not running")
            return False

        bot_process = self.processes[bot_name]
        logger.info(f"Stopping {bot_process.config.display_name}...")

        try:
            # Send SIGTERM for graceful shutdown
            bot_process.process.terminate()

            # Wait for process to exit
            try:
                bot_process.process.wait(timeout=timeout)
                logger.info(f"✅ Stopped {bot_process.config.display_name}")
                return True
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                logger.warning(f"Force killing {bot_process.config.display_name}")
                bot_process.process.kill()
                bot_process.process.wait()
                return True

        except Exception as e:
            logger.error(f"Error stopping {bot_process.config.display_name}: {e}")
            return False

        finally:
            del self.processes[bot_name]

    def _check_bot_health(self, bot_name: str) -> bool:
        """Check if a bot process is still running."""
        if bot_name not in self.processes:
            return False

        bot_process = self.processes[bot_name]
        poll_result = bot_process.process.poll()

        if poll_result is not None:
            # Process has exited - capture and log any error output

            logger.warning(
                f"{bot_process.config.display_name} exited with code {poll_result}"
            )
            return False

        return True

    def _restart_bot(self, bot_name: str) -> bool:
        """Restart a bot (stop then start)."""
        logger.info(f"Restarting {bot_name}...")

        # Stop if running
        if bot_name in self.processes:
            self._stop_bot(bot_name)

        # Wait restart delay
        delay = self.defaults.get('restart_delay_seconds', 10)
        logger.info(f"Waiting {delay}s before restart...")
        time.sleep(delay)

        # Start bot
        if bot_name in self.bots:
            bot_config = self.bots[bot_name]
            bot_process = self._start_bot(bot_config)

            if bot_process:
                bot_process.restart_count = self.processes.get(bot_name, bot_process).restart_count + 1
                bot_process.last_restart = datetime.now()
                self.processes[bot_name] = bot_process
                return True

        return False

    def _monitor_loop(self) -> None:
        """Main monitoring loop - checks bot health and restarts if needed."""
        logger.info("Starting monitoring loop...")

        while self.running:
            # Check each bot's health
            for bot_name in list(self.processes.keys()):
                if not self._check_bot_health(bot_name):
                    logger.warning(f"{bot_name} is not healthy, restarting...")
                    self._restart_bot(bot_name)

            # Monitor interval
            time.sleep(5)

        logger.info("Monitoring loop stopped")

    def start_all(self, bot_names: Optional[List[str]] = None) -> None:
        """Start all enabled bots or specific bots."""
        self.running = True

        # Determine which bots to start
        if bot_names:
            bots_to_start = {name: self.bots[name] for name in bot_names if name in self.bots}
        else:
            bots_to_start = {name: bot for name, bot in self.bots.items() if bot.enabled}

        logger.info(f"Starting {len(bots_to_start)} bots...")

        # Start each bot
        for bot_name, bot_config in bots_to_start.items():
            bot_process = self._start_bot(bot_config)
            if bot_process:
                self.processes[bot_name] = bot_process

        if not self.processes:
            logger.error("No bots started, exiting")
            sys.exit(1)

        logger.info(f"Started {len(self.processes)} bots successfully")

        # Enter monitoring loop
        try:
            self._monitor_loop()
        finally:
            self.stop_all()

    def stop_all(self) -> None:
        """Stop all running bots."""
        logger.info("Stopping all bots...")

        for bot_name in list(self.processes.keys()):
            self._stop_bot(bot_name)

        logger.info("All bots stopped")

    def list_bots(self) -> None:
        """List all configured bots."""
        print("\n📋 Configured Bots:\n")
        for bot in self.bots.values():
            status = "✅ ENABLED" if bot.enabled else "⏸️  DISABLED"
            print(f"  {bot.name:10s} | {bot.symbol:6s} | Client ID: {bot.client_id} | {status}")
            print(f"             {bot.description}")
            print()

    def status(self) -> None:
        """Show status of all bots."""
        print("\n📊 Bot Status:\n")

        for bot_name, bot_config in self.bots.items():
            if bot_name in self.processes:
                bot_process = self.processes[bot_name]
                uptime = datetime.now() - bot_process.start_time
                uptime_str = str(uptime).split('.')[0]  # Remove microseconds

                print(f"  {bot_config.display_name:20s} | ✅ RUNNING")
                print(f"    PID: {bot_process.process.pid}")
                print(f"    Uptime: {uptime_str}")
                print(f"    Restarts: {bot_process.restart_count}")
                if bot_process.last_restart:
                    print(f"    Last Restart: {bot_process.last_restart}")
            else:
                status = "⏸️  DISABLED" if not bot_config.enabled else "⏹️  STOPPED"
                print(f"  {bot_config.display_name:20s} | {status}")
            print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Universal Trading Bot Manager")
    parser.add_argument('--bots', nargs='+', help='Specific bots to start (by name)')
    parser.add_argument('--list', action='store_true', help='List all configured bots')
    parser.add_argument('--status', action='store_true', help='Show bot status')
    parser.add_argument('--config', default='config/bots.json', help='Path to config file')

    args = parser.parse_args()

    # Create manager
    manager = BotManager(config_file=args.config)

    # Handle commands
    if args.list:
        manager.list_bots()
        return

    if args.status:
        manager.status()
        return

    # Start bots
    logger.info("=" * 60)
    logger.info("Universal Trading Bot Manager v3.0")
    logger.info("=" * 60)

    manager.start_all(bot_names=args.bots)


if __name__ == "__main__":
    main()

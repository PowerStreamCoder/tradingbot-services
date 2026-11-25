"""
Unit tests for gateway configurations and scripts (Phase 1).
"""

import os
import unittest
import yaml
import subprocess


class TestGatewayConfiguration(unittest.TestCase):

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.compose_file = os.path.join(self.repo_root, "docker-compose.gateways.yml")
        self.manage_script = os.path.join(self.repo_root, "scripts", "manage-gateways.sh")
        self.health_script = os.path.join(self.repo_root, "scripts", "health-check.sh")
        self.watchdog_script = os.path.join(self.repo_root, "scripts", "ops-watchdog.sh")
        self.backup_data_script = os.path.join(self.repo_root, "scripts", "backup-trading-data.sh")
        self.gitignore_file = os.path.join(self.repo_root, ".gitignore")

    def test_docker_compose_valid_and_constrained(self):
        """Verify docker-compose.gateways.yml has valid syntax, memory caps, and loopback ports."""
        self.assertTrue(os.path.exists(self.compose_file), "docker-compose.gateways.yml must exist")

        with open(self.compose_file, "r") as f:
            data = yaml.safe_load(f)

        self.assertIn("services", data)
        services = data["services"]

        self.assertIn("ib-gateway-paper", services)
        self.assertIn("ib-gateway-live", services)

        for svc_name in ["ib-gateway-paper", "ib-gateway-live"]:
            svc = services[svc_name]
            # Verify memory and swap limits
            self.assertEqual(svc.get("mem_limit"), "512m")
            self.assertEqual(svc.get("memswap_limit"), "512m")

            # Verify JVM heap ceiling via JAVA_HEAP_SIZE (numeric MB, Blocker 1 fix)
            env = svc.get("environment", [])
            self.assertIn(
                "JAVA_HEAP_SIZE=384", env,
                f"Service '{svc_name}' must configure JAVA_HEAP_SIZE=384 for gnzsnz/ib-gateway"
            )

            # Verify loopback-only port bindings
            ports = svc.get("ports", [])
            for p in ports:
                self.assertTrue(
                    p.startswith("127.0.0.1:"),
                    f"Port binding '{p}' in service '{svc_name}' must be loopback only (127.0.0.1)"
                )

    def test_manage_gateways_invalid_command(self):
        """Edge case: manage-gateways.sh exits with code 1 and usage on invalid command."""
        res = subprocess.run([self.manage_script, "invalid-cmd"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Usage:", res.stdout + res.stderr)

    def test_manage_gateways_verify_ports(self):
        """Edge case: manage-gateways.sh verify-ports runs without syntax error."""
        res = subprocess.run([self.manage_script, "verify-ports"], capture_output=True, text=True)
        # Exits cleanly and produces output for the 6 ports
        for p in ["4001", "4002", "4003", "4004", "5900", "5901"]:
            self.assertIn(f"Port {p}:", res.stdout)

    def test_manage_gateways_bash_syntax(self):
        """Verify manage-gateways.sh passes bash syntax check."""
        res = subprocess.run(["bash", "-n", self.manage_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in manage-gateways.sh: {res.stderr}")

    def test_health_check_bash_syntax(self):
        """Verify health-check.sh passes bash syntax check."""
        res = subprocess.run(["bash", "-n", self.health_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in health-check.sh: {res.stderr}")

    def test_ops_watchdog_bash_syntax(self):
        """Verify ops-watchdog.sh passes bash syntax check."""
        res = subprocess.run(["bash", "-n", self.watchdog_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in ops-watchdog.sh: {res.stderr}")

    def test_all_operational_scripts_syntax(self):
        """Verify all scripts/*.sh pass bash syntax check."""
        scripts_dir = os.path.join(self.repo_root, "scripts")
        for fname in os.listdir(scripts_dir):
            if fname.endswith(".sh"):
                fpath = os.path.join(scripts_dir, fname)
                res = subprocess.run(["bash", "-n", fpath], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"Syntax error in {fname}: {res.stderr}")

    def test_gitignore_contains_secrets_rules(self):
        """Verify .gitignore includes gateway secret rules."""
        with open(self.gitignore_file, "r") as f:
            content = f.read()
        self.assertIn("*.ibgateway*.env", content)
        self.assertIn("*.env", content)

    def test_trade_log_auditor_service_dual_gateway_env(self):
        """Verify trade-log-auditor.service configures dual gateway ports, client IDs, and auto probe."""
        service_file = os.path.join(self.repo_root, "trade-log-auditor.service")
        self.assertTrue(os.path.exists(service_file), "trade-log-auditor.service must exist")

        with open(service_file, "r") as f:
            content = f.read()

        # Phase 4 Mandatory 3 expectations
        self.assertIn("Environment=AUDITOR_BROKER_PORT=4002", content)
        self.assertIn("Environment=AUDITOR_BROKER_PORT_PAPER=4002", content)
        self.assertIn("Environment=AUDITOR_CLIENT_ID_PAPER=20", content)
        self.assertIn("Environment=AUDITOR_BROKER_PORT_LIVE=4001", content)
        self.assertIn("Environment=AUDITOR_CLIENT_ID_LIVE=21", content)
        self.assertIn("Environment=CHECK_LIVE_GATEWAY=auto", content)

        # Verification of no client ID collision with bots (3-8) or flatness gate (22)
        bot_ids = {"3", "4", "5", "6", "7", "8"}
        flatness_id = "22"
        self.assertNotIn("AUDITOR_CLIENT_ID_PAPER=" + flatness_id, content)
        self.assertNotIn("AUDITOR_CLIENT_ID_LIVE=" + flatness_id, content)
        for bid in bot_ids:
            self.assertNotIn("AUDITOR_CLIENT_ID_PAPER=" + bid, content)
            self.assertNotIn("AUDITOR_CLIENT_ID_LIVE=" + bid, content)

    def test_trade_log_auditor_exit_semantics(self):
        """#155 AUD-005: high findings fail; inconclusive and busy do not."""
        service_file = os.path.join(self.repo_root, "trade-log-auditor.service")
        with open(service_file, "r") as f:
            content = f.read()

        self.assertIn("SuccessExitStatus=0 3 4", content)
        self.assertIn("critical/high findings", content)
        self.assertIn("LOCK_BUSY", content)

    def test_live_gateway_market_hours_gating_in_watchdog_and_health_scripts(self):
        """Verify ops-watchdog.sh and health-check.sh gate live gateway checks to market hours."""
        import tempfile
        for script_path in [self.watchdog_script, self.health_script]:
            script_name = os.path.basename(script_path)
            with open(script_path, "r") as f:
                content = f.read()

            self.assertIn("LIVE_GATEWAY_MARKET_HOURS_ONLY", content, f"{script_name} must configure LIVE_GATEWAY_MARKET_HOURS_ONLY")
            self.assertIn("is_live_trading_window", content, f"{script_name} must define is_live_trading_window")

            with tempfile.TemporaryDirectory() as td:
                mock_date = os.path.join(td, "date")
                with open(mock_date, "w") as f:
                    f.write("""#!/bin/bash
if [ "$1" = "+%u" ]; then
    echo "${MOCK_DOW:-1}"
elif [ "$1" = "+%H%M" ]; then
    echo "${MOCK_HM:-1200}"
else
    /bin/date "$@"
fi
""")
                os.chmod(mock_date, 0o755)

                test_cmd = f"""
                export PATH="{td}:$PATH"
                eval "$(sed -n '/^is_live_trading_window()/,/^}}/p' '{script_path}')"

                # 1. Unconstrained when LIVE_GATEWAY_MARKET_HOURS_ONLY=0
                LIVE_GATEWAY_MARKET_HOURS_ONLY=0 MOCK_DOW=6 MOCK_HM=0100 is_live_trading_window || exit 1

                # 2. Weekends -> out of window (exit code 1 from function)
                MOCK_DOW=6 MOCK_HM=1200 is_live_trading_window && exit 2 # Saturday
                MOCK_DOW=7 MOCK_HM=1200 is_live_trading_window && exit 3 # Sunday

                # 3. Off-hours weekdays -> out of window
                MOCK_DOW=4 MOCK_HM=0859 is_live_trading_window && exit 4 # 08:59 ET
                MOCK_DOW=4 MOCK_HM=1701 is_live_trading_window && exit 5 # 17:01 ET
                MOCK_DOW=4 MOCK_HM=2000 is_live_trading_window && exit 6 # 20:00 ET (01:00 UTC overnight)

                # 4. In-window weekdays -> in window (exit code 0 from function)
                MOCK_DOW=1 MOCK_HM=0900 is_live_trading_window || exit 7 # Mon 09:00 ET
                MOCK_DOW=3 MOCK_HM=1430 is_live_trading_window || exit 8 # Wed 14:30 ET
                MOCK_DOW=5 MOCK_HM=1700 is_live_trading_window || exit 9 # Fri 17:00 ET

                exit 0
                """
                res = subprocess.run(["bash", "-c", test_cmd], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"Market hours gating test failed for {script_name} (code {res.returncode}): {res.stderr}")

    def test_watchdog_backup_hygiene_split_alerts(self):
        """Verify ops-watchdog synchronizes alerts with FIFO, TTL, size, integrity, and freshness."""
        import tempfile
        import shutil
        import tarfile

        td = tempfile.mkdtemp(prefix="watchdog_test_")
        try:
            logs_dir = os.path.join(td, "logs")
            state_dir = os.path.join(logs_dir, "health-state")
            backups_dir = os.path.join(td, "backups")
            os.makedirs(state_dir, exist_ok=True)
            os.makedirs(backups_dir, exist_ok=True)

            legacy_marker = os.path.join(state_dir, "hygiene-backups")
            legacy_age_marker = os.path.join(state_dir, "hygiene-backups-age")
            size_marker = os.path.join(state_dir, "hygiene-backups-size")
            fifo_marker = os.path.join(state_dir, "hygiene-backups-fifo")
            dumps_marker = os.path.join(state_dir, "hygiene-backups-dumps")
            integrity_marker = os.path.join(state_dir, "hygiene-backups-integrity")
            freshness_marker = os.path.join(state_dir, "hygiene-backups-freshness")

            # Seed legacy markers to verify they are automatically cleared
            with open(legacy_marker, "w") as f:
                f.write("old")
            with open(legacy_age_marker, "w") as f:
                f.write("old")

            trading_data_dir = os.path.join(backups_dir, "trading-data")
            services_dir = os.path.join(backups_dir, "services")
            os.makedirs(trading_data_dir, exist_ok=True)
            os.makedirs(services_dir, exist_ok=True)

            dummy_src = os.path.join(td, "dummy.txt")
            with open(dummy_src, "w") as f:
                f.write("data")

            # 1. Healthy state: 3 valid archives, latest is fresh (<50h)
            for i in range(3):
                fpath = os.path.join(trading_data_dir, f"trading-state-2026100{i}.tar.gz")
                with tarfile.open(fpath, "w:gz") as tar:
                    tar.add(dummy_src, arcname="dummy.txt")

                sdir = os.path.join(services_dir, f"backup-2026100{i}-120000")
                os.makedirs(sdir, exist_ok=True)

            # Fresh operational profile dump (< 30 days old)
            fresh_profile = os.path.join(backups_dir, "trading_profile_backup_fresh.txt")
            with open(fresh_profile, "w") as f:
                f.write("paper")

            env = os.environ.copy()
            env["OPS_WATCHDOG_LOG_DIR"] = logs_dir
            env["BACKUP_DIR"] = backups_dir
            env["OPS_WATCHDOG_DRY_RUN"] = "1"
            env["BACKUP_MAX_GB"] = "1"
            env["BACKUP_MAX_AGE_DAYS"] = "30"
            env["BACKUP_MAX_HOURS"] = "50"

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertFalse(os.path.exists(legacy_marker), "Legacy marker must be cleared on healthy run")
            self.assertFalse(os.path.exists(legacy_age_marker), "Legacy age marker must be cleared")
            self.assertFalse(os.path.exists(size_marker), "Size marker must not trigger for small backup")
            self.assertFalse(os.path.exists(fifo_marker), "FIFO marker must not trigger when count <= 3")
            self.assertFalse(os.path.exists(dumps_marker), "Dumps marker must not trigger for fresh dumps")
            self.assertFalse(os.path.exists(integrity_marker), "Integrity marker must not trigger for valid tarball")
            self.assertFalse(os.path.exists(freshness_marker), "Freshness marker must not trigger for fresh tarball")

            # 2. FIFO Violation: Add a 4th backup to trading-data (exceeds FIFO limit of 3)
            date_marker = os.path.join(state_dir, "hygiene-last-run-date")
            if os.path.exists(date_marker):
                os.remove(date_marker)
            fourth_backup = os.path.join(trading_data_dir, "trading-state-extra.tar.gz")
            with tarfile.open(fourth_backup, "w:gz") as tar:
                tar.add(dummy_src, arcname="dummy.txt")

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(os.path.exists(fifo_marker), "FIFO marker must be created when count > 3")

            # 3. Operational Dump TTL Violation: Add a profile switch dump > 30 days old
            if os.path.exists(date_marker):
                os.remove(date_marker)
            old_time = 1000000000
            stale_profile = os.path.join(backups_dir, "trading_profile_backup_old.txt")
            with open(stale_profile, "w") as f:
                f.write("live")
            os.utime(stale_profile, (old_time, old_time))

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(os.path.exists(dumps_marker), "Dumps marker must trigger for operational dumps > 30 days")

            # 4. Integrity Violation: Corrupt the latest archive (write garbage)
            if os.path.exists(date_marker):
                os.remove(date_marker)
            corrupt_backup = os.path.join(trading_data_dir, "trading-state-latest-corrupt.tar.gz")
            with open(corrupt_backup, "wb") as f:
                f.write(b"this is corrupt not gzip")

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(os.path.exists(integrity_marker), "Integrity marker must trigger for corrupt tarball")

            # Verify alert text in log
            alerts_log = os.path.join(logs_dir, "health-alerts.log")
            with open(alerts_log, "r") as f:
                content = f.read()
            self.assertIn("FIFO backup retention limit exceeded", content)
            self.assertIn("Latest trading data backup is corrupted", content)
            self.assertIn("Stale operational backup dumps found", content)

        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_watchdog_source_hygiene_ignores_runtime_state_backups(self):
        """Verify ops-watchdog ignores runtime trader_state_*.json.bak in data/ but flags source tree cruft."""
        import tempfile
        import shutil

        td = tempfile.mkdtemp(prefix="watchdog_cruft_test_")
        try:
            logs_dir = os.path.join(td, "logs")
            state_dir = os.path.join(logs_dir, "health-state")
            src_dir = os.path.join(td, "tradingbots")
            os.makedirs(state_dir, exist_ok=True)
            os.makedirs(os.path.join(src_dir, "data", "afrm"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, ".git"), exist_ok=True)
            os.makedirs(os.path.join(src_dir, ".venv"), exist_ok=True)

            # Legitimate runtime state backups and ignored trees
            with open(os.path.join(src_dir, "data", "afrm", "trader_state_afrm.json.bak"), "w") as f:
                f.write('{"status": "ok"}')
            with open(os.path.join(src_dir, ".git", "hook.bak"), "w") as f:
                f.write("git hook backup")
            with open(os.path.join(src_dir, ".venv", "pip.bak"), "w") as f:
                f.write("venv backup")

            env = os.environ.copy()
            env["OPS_WATCHDOG_LOG_DIR"] = logs_dir
            env["HYGIENE_DIRS"] = src_dir
            env["OPS_WATCHDOG_DRY_RUN"] = "1"
            backups_dir = os.path.join(td, "backups")
            os.makedirs(backups_dir, exist_ok=True)
            env["BACKUP_DIR"] = backups_dir

            cruft_marker = os.path.join(state_dir, "hygiene-cruft")

            # 1. Run watchdog: state backups in data/ should NOT trigger hygiene-cruft
            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertFalse(os.path.exists(cruft_marker), "hygiene-cruft must not trigger for state backups in data/")

            # 2. Add real cruft in source tree: should trigger hygiene-cruft
            date_marker = os.path.join(state_dir, "hygiene-last-run-date")
            if os.path.exists(date_marker):
                os.remove(date_marker)
            stray_bak = os.path.join(src_dir, "bot.py.bak")
            with open(stray_bak, "w") as f:
                f.write("stray edit")

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(os.path.exists(cruft_marker), "hygiene-cruft must trigger for stray .bak in source tree")

            # 3. Clean up the stray cruft: should recover and clear marker
            if os.path.exists(date_marker):
                os.remove(date_marker)
            os.remove(stray_bak)

            res = subprocess.run(["bash", self.watchdog_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertFalse(os.path.exists(cruft_marker), "hygiene-cruft marker must be cleared on recovery")

        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_backup_trading_data_script_verification_and_gated_pruning(self):
        """Verify backup-trading-data.sh creates verified archives, enforces FIFO, and gates pruning."""
        import tempfile
        import shutil
        import tarfile

        td = tempfile.mkdtemp(prefix="backup_script_test_")
        try:
            fake_home = os.path.join(td, "home")
            backup_dir = os.path.join(td, "backups")
            data_dir = os.path.join(fake_home, "tradingbots", "data")
            config_dir = os.path.join(fake_home, "tradingbot-config")
            os.makedirs(data_dir, exist_ok=True)
            os.makedirs(config_dir, exist_ok=True)

            with open(os.path.join(data_dir, "state.json"), "w") as f:
                f.write('{"status": "ok"}')
            with open(os.path.join(config_dir, "bots.json"), "w") as f:
                f.write('{"bots": []}')

            env = os.environ.copy()
            env["HOME"] = fake_home
            env["BACKUP_DIR"] = backup_dir

            # 1. Run backup script
            res = subprocess.run(["bash", self.backup_data_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Backup script failed: {res.stderr}")
            self.assertIn("Backup created and verified:", res.stdout)

            # Check created file
            backups = [f for f in os.listdir(backup_dir) if f.startswith("trading-state-") and f.endswith(".tar.gz")]
            self.assertEqual(len(backups), 1)

            # Verify contents of archive
            archive_path = os.path.join(backup_dir, backups[0])
            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("tradingbots/data", names)
                self.assertIn("tradingbot-config", names)

            # 2. Run 3 more times to verify FIFO (keep max 3)
            for _ in range(3):
                import time
                time.sleep(1.05)  # Ensure unique second timestamp
                res = subprocess.run(["bash", self.backup_data_script], env=env, capture_output=True, text=True)
                self.assertEqual(res.returncode, 0)

            backups = [f for f in os.listdir(backup_dir) if f.startswith("trading-state-") and f.endswith(".tar.gz")]
            self.assertEqual(len(backups), 3, "FIFO must prune oldest backups and keep strictly 3")

            # 3. Verify failure behavior: empty directory should exit 1 and not create corrupt archive
            empty_home = os.path.join(td, "empty_home")
            os.makedirs(empty_home, exist_ok=True)
            env["HOME"] = empty_home
            res = subprocess.run(["bash", self.backup_data_script], env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, "Script must fail when no data exists to back up")

            # 4. Verify operational dump pruning in parent backup directory (>30 days)
            env["HOME"] = fake_home
            parent_dir = os.path.dirname(backup_dir)
            old_time = 1000000000
            stale_dump = os.path.join(parent_dir, "trading_profile_backup_old.txt")
            fresh_dump = os.path.join(parent_dir, "trading_profile_backup_fresh.txt")
            with open(stale_dump, "w") as f:
                f.write("paper")
            with open(fresh_dump, "w") as f:
                f.write("paper")
            os.utime(stale_dump, (old_time, old_time))

            res = subprocess.run(["bash", self.backup_data_script], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertFalse(os.path.exists(stale_dump), "Stale operational dumps (>30 days) must be pruned")
            self.assertTrue(os.path.exists(fresh_dump), "Fresh operational dumps must be retained")

        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_ops_watchdog_weekend_tolerance_uses_eastern_timezone(self):
        """Verify ops-watchdog evaluates day-of-week in America/New_York for weekend tolerance."""
        with open(self.watchdog_script, "r") as f:
            content = f.read()

        # The global dow calculation for weekend tolerance must evaluate in America/New_York
        self.assertIn('dow=$(TZ="America/New_York" date +%u 2>/dev/null || date +%u)', content,
                      "Weekend tolerance DOW calculation must use America/New_York timezone to prevent false alerts on Monday evening (Tuesday UTC)")


class TestWatchdogAuditVerdict(unittest.TestCase):
    """#155 AUD-014: silent audit verdicts must page within one watchdog cycle."""

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.watchdog_script = os.path.join(self.repo_root, "scripts", "ops-watchdog.sh")

    @staticmethod
    def _write_run(reports_dir, name, created_at, status="success",
                   dispatch=None, errors=None, reasons=None):
        import json
        payload = {
            "run_id": name,
            "created_at": created_at,
            "status": status,
            "dispatch": dispatch if dispatch is not None else {"errors": []},
            "errors": errors or [],
            "inconclusive_reasons": reasons or [],
        }
        with open(os.path.join(reports_dir, f"2026-11-18_ALL_{name}.json"), "w") as f:
            json.dump(payload, f)

    def _run_watchdog(self, tmp, reports_dir):
        import shutil
        logs_dir = os.path.join(tmp, "logs")
        state_dir = os.path.join(logs_dir, "health-state")
        os.makedirs(state_dir, exist_ok=True)
        env = os.environ.copy()
        env.update({
            "OPS_WATCHDOG_LOG_DIR": logs_dir,
            "AUDITOR_REPORTS_DIR": reports_dir,
            "OPS_WATCHDOG_DRY_RUN": "1",
            "PYTHON_BIN": "python3",
        })
        res = subprocess.run(["bash", self.watchdog_script], env=env,
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return state_dir, os.path.join(logs_dir, "health-alerts.log"), shutil

    def test_two_inconclusive_runs_page(self):
        import tempfile
        import shutil

        td = tempfile.mkdtemp(prefix="watchdog_audit_inc_")
        try:
            reports = os.path.join(td, "reports")
            os.makedirs(reports, exist_ok=True)
            reasons = ["Firestore init failed", "required source empty"]
            self._write_run(reports, "r1", "2026-11-17T00:00:00", status="inconclusive",
                            reasons=reasons)
            self._write_run(reports, "r2", "2026-11-18T00:00:00", status="inconclusive",
                            reasons=reasons)
            state_dir, alert_log, _ = self._run_watchdog(td, reports)

            self.assertTrue(os.path.exists(os.path.join(state_dir, "audit-inconclusive")))
            with open(alert_log) as f:
                content = f.read()
            self.assertIn("Two consecutive inconclusive audit runs", content)
            self.assertIn("Firestore init failed", content)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_single_inconclusive_run_does_not_page(self):
        import tempfile
        import shutil

        td = tempfile.mkdtemp(prefix="watchdog_audit_single_")
        try:
            reports = os.path.join(td, "reports")
            os.makedirs(reports, exist_ok=True)
            self._write_run(reports, "r1", "2026-11-17T00:00:00", status="inconclusive",
                            reasons=["one-off source blip"])
            self._write_run(reports, "r2", "2026-11-18T00:00:00")
            state_dir, _, _ = self._run_watchdog(td, reports)
            self.assertFalse(os.path.exists(os.path.join(state_dir, "audit-inconclusive")))
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_dispatch_error_pages(self):
        import tempfile
        import shutil

        td = tempfile.mkdtemp(prefix="watchdog_audit_dispatch_")
        try:
            reports = os.path.join(td, "reports")
            os.makedirs(reports, exist_ok=True)
            self._write_run(reports, "r1", "2026-11-17T00:00:00")
            self._write_run(reports, "r2", "2026-11-18T00:00:00",
                            dispatch={"errors": ["critical_alert: SMTP 535 auth failure"]})
            state_dir, alert_log, _ = self._run_watchdog(td, reports)

            self.assertTrue(os.path.exists(os.path.join(state_dir, "audit-dispatch-error")))
            with open(alert_log) as f:
                content = f.read()
            self.assertIn("Audit alert dispatch failed", content)
            self.assertIn("SMTP 535 auth failure", content)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

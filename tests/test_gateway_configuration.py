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


if __name__ == "__main__":
    unittest.main()

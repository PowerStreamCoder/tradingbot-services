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


if __name__ == "__main__":
    unittest.main()

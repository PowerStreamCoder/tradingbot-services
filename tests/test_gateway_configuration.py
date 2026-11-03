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

    def test_docker_compose_valid_and_constrained(self):
        """Verify docker-compose.gateways.yml has valid syntax and strictly loopback-bound ports."""
        self.assertTrue(os.path.exists(self.compose_file), "docker-compose.gateways.yml must exist")

        with open(self.compose_file, "r") as f:
            data = yaml.safe_load(f)

        self.assertIn("services", data)
        services = data["services"]

        self.assertIn("ib-gateway-paper", services)
        self.assertIn("ib-gateway-live", services)

        for svc_name in ["ib-gateway-paper", "ib-gateway-live"]:
            svc = services[svc_name]
            # Verify memory limit
            self.assertEqual(svc.get("mem_limit"), "512m")
            deploy_limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
            self.assertEqual(deploy_limits.get("memory"), "512m")

            # Verify JVM heap limit
            env = svc.get("environment", [])
            self.assertIn("JAVA_OPTS=-Xmx384m", env)

            # Verify loopback-only port bindings
            ports = svc.get("ports", [])
            for p in ports:
                self.assertTrue(
                    p.startswith("127.0.0.1:"),
                    f"Port binding '{p}' in service '{svc_name}' must be loopback only (127.0.0.1)"
                )

    def test_manage_gateways_bash_syntax(self):
        """Verify manage-gateways.sh passes bash syntax check."""
        res = subprocess.run(["bash", "-n", self.manage_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in manage-gateways.sh: {res.stderr}")

    def test_health_check_bash_syntax(self):
        """Verify health-check.sh passes bash syntax check."""
        res = subprocess.run(["bash", "-n", self.health_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in health-check.sh: {res.stderr}")


if __name__ == "__main__":
    unittest.main()

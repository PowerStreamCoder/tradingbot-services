"""
Unit tests for smoke-test-live-gateway.py (Phase 1 Mandatory Live Broker Gate).
"""

import socket
import threading
import socketserver
import unittest
from unittest.mock import MagicMock, patch
import importlib.util
import os

# Import smoke-test-live-gateway dynamically from scripts/
script_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "smoke-test-live-gateway.py")
)
spec = importlib.util.spec_from_file_location("smoke_test_module", script_path)
smoke_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_test)


class SimpleTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class TestSmokeTestLiveGateway(unittest.TestCase):

    def setUp(self):
        # Start a local TCP server on an ephemeral port
        self.server = SimpleTCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
        self.server_port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_tcp_socket_success(self):
        """test_tcp_socket should succeed on an open TCP port."""
        ok, msg = smoke_test.test_tcp_socket("127.0.0.1", self.server_port, timeout=1.0)
        self.assertTrue(ok)
        self.assertIn("succeeded", msg)

    def test_tcp_socket_failure(self):
        """test_tcp_socket should fail on an unallocated port."""
        unused_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unused_sock.bind(("127.0.0.1", 0))
        unused_port = unused_sock.getsockname()[1]
        unused_sock.close()

        ok, msg = smoke_test.test_tcp_socket("127.0.0.1", unused_port, timeout=0.5)
        self.assertFalse(ok)
        self.assertIn("failed", msg)

    def test_run_socket_checks_success(self):
        """run_socket_checks returns (True, True) when both ports are reachable."""
        live_ok, paper_ok = smoke_test.run_socket_checks(
            live_host="127.0.0.1",
            live_port=self.server_port,
            paper_host="127.0.0.1",
            paper_port=self.server_port,
            timeout=1.0
        )
        self.assertTrue(live_ok)
        self.assertTrue(paper_ok)

    def test_run_socket_checks_failure_when_live_down(self):
        """run_socket_checks returns False for live_ok when live port is unreachable."""
        unused_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unused_sock.bind(("127.0.0.1", 0))
        unused_port = unused_sock.getsockname()[1]
        unused_sock.close()

        live_ok, paper_ok = smoke_test.run_socket_checks(
            live_host="127.0.0.1",
            live_port=unused_port,
            paper_host="127.0.0.1",
            paper_port=self.server_port,
            timeout=0.5
        )
        self.assertFalse(live_ok)

    def test_broker_handshake_rejects_paper_account(self):
        """CRITICAL: Broker handshake MUST fail if Live Gateway returns DU (Paper) account."""
        mock_ib_class = MagicMock()
        mock_ib_inst = MagicMock()
        mock_ib_class.return_value = mock_ib_inst

        mock_ib_inst.isConnected.return_value = True
        mock_ib_inst.managedAccounts.return_value = ["DU9999999"]

        mock_util = MagicMock()
        mock_stock = MagicMock()

        mock_ib_insync = MagicMock()
        mock_ib_insync.IB = mock_ib_class
        mock_ib_insync.Stock = mock_stock
        mock_ib_insync.util = mock_util

        with patch.dict("sys.modules", {"ib_insync": mock_ib_insync}):
            result = smoke_test.run_broker_handshake(
                live_host="127.0.0.1",
                live_port=4001,
                paper_host="127.0.0.1",
                paper_port=4002,
                client_id=99,
                skip_market_data=True,
                paper_socket_reachable=False
            )
            # Must return False because DU account on live port is forbidden!
            self.assertFalse(result)

    def test_broker_handshake_rejects_unexpected_prefix_account(self):
        """CRITICAL (Blocker 4): Handshake MUST fail closed on non-U/non-DU account prefix."""
        mock_ib_class = MagicMock()
        mock_ib_inst = MagicMock()
        mock_ib_class.return_value = mock_ib_inst

        mock_ib_inst.isConnected.return_value = True
        mock_ib_inst.managedAccounts.return_value = ["A9876543"]

        mock_util = MagicMock()
        mock_stock = MagicMock()

        mock_ib_insync = MagicMock()
        mock_ib_insync.IB = mock_ib_class
        mock_ib_insync.Stock = mock_stock
        mock_ib_insync.util = mock_util

        with patch.dict("sys.modules", {"ib_insync": mock_ib_insync}):
            result = smoke_test.run_broker_handshake(
                live_host="127.0.0.1",
                live_port=4001,
                paper_host="127.0.0.1",
                paper_port=4002,
                client_id=99,
                skip_market_data=True,
                paper_socket_reachable=False
            )
            # Must fail closed on unrecognized prefix!
            self.assertFalse(result)

    def test_broker_handshake_accepts_live_account(self):
        """Broker handshake passes when Live Gateway returns U (Live) account."""
        mock_ib_class = MagicMock()
        mock_ib_inst = MagicMock()
        mock_ib_class.return_value = mock_ib_inst

        mock_ib_inst.isConnected.return_value = True
        mock_ib_inst.managedAccounts.return_value = ["U1234567"]

        mock_util = MagicMock()
        mock_stock = MagicMock()

        mock_ib_insync = MagicMock()
        mock_ib_insync.IB = mock_ib_class
        mock_ib_insync.Stock = mock_stock
        mock_ib_insync.util = mock_util

        with patch.dict("sys.modules", {"ib_insync": mock_ib_insync}):
            result = smoke_test.run_broker_handshake(
                live_host="127.0.0.1",
                live_port=4001,
                paper_host="127.0.0.1",
                paper_port=4002,
                client_id=99,
                skip_market_data=True,
                paper_socket_reachable=False
            )
            self.assertTrue(result)

    def test_verify_market_data_failure_fails_gate(self):
        """Major 3: Handshake MUST fail if live market data quote fails/times out."""
        mock_ib_class = MagicMock()
        mock_ib_inst = MagicMock()
        mock_ib_class.return_value = mock_ib_inst

        mock_ib_inst.isConnected.return_value = True
        mock_ib_inst.managedAccounts.return_value = ["U1234567"]

        # Mock ticker that returns None for price (times out)
        mock_ticker = MagicMock()
        mock_ticker.marketPrice.return_value = None
        mock_ticker.last = None
        mock_ticker.close = None
        mock_ib_inst.reqMktData.return_value = mock_ticker

        mock_util = MagicMock()
        mock_stock = MagicMock()

        mock_ib_insync = MagicMock()
        mock_ib_insync.IB = mock_ib_class
        mock_ib_insync.Stock = mock_stock
        mock_ib_insync.util = mock_util

        with patch.dict("sys.modules", {"ib_insync": mock_ib_insync}):
            with patch("time.time", side_effect=[100.0, 106.0]):
                result = smoke_test.run_broker_handshake(
                    live_host="127.0.0.1",
                    live_port=4001,
                    paper_host="127.0.0.1",
                    paper_port=4002,
                    client_id=99,
                    skip_market_data=False,
                    paper_socket_reachable=False
                )
                # Market data failed -> Gate must fail!
                self.assertFalse(result)

    def test_paper_cross_check_rejects_non_du_account(self):
        """Major 5: Paper cross-check MUST fail if paper port is cross-wired to a non-DU account."""
        mock_ib_class = MagicMock()
        mock_ib_inst = MagicMock()
        mock_ib_class.return_value = mock_ib_inst

        mock_ib_inst.isConnected.return_value = True
        # First call for live port returns U1234567, second call for paper port returns U7654321 (cross-wire!)
        mock_ib_inst.managedAccounts.side_effect = [["U1234567"], ["U7654321"]]

        mock_util = MagicMock()
        mock_stock = MagicMock()

        mock_ib_insync = MagicMock()
        mock_ib_insync.IB = mock_ib_class
        mock_ib_insync.Stock = mock_stock
        mock_ib_insync.util = mock_util

        with patch.dict("sys.modules", {"ib_insync": mock_ib_insync}):
            result = smoke_test.run_broker_handshake(
                live_host="127.0.0.1",
                live_port=4001,
                paper_host="127.0.0.1",
                paper_port=4002,
                client_id=99,
                skip_market_data=True,
                paper_socket_reachable=True
            )
            # Paper port returning U account means dangerous cross-wire -> MUST fail!
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

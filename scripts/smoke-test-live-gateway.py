#!/usr/bin/env python3
"""
Zero-Size Live Broker Smoke Test Script (Phase 1 Mandatory Gate).

Ref: SIMULTANEOUS_PAPER_LIVE_TRADING_DESIGN.md §8 (Phase 1), Issue #109.

Verifies:
1. Socket reachability on Live Gateway (:4001) and Paper Gateway (:4002).
2. Live IBKR Broker Handshake: confirms account ID strictly starts with 'U' (Live)
   and fails closed on 'DU' or any unexpected prefix across all returned accounts.
3. Market Data Quote Subscription: confirms live quotes for liquid contract (SPY).
4. Paper Gateway Isolation: confirms Paper Gateway (:4002) is unaffected
   and strictly serving paper account (DU...). Fails on cross-wire.

Modes:
- Standard / Broker Mode: Uses ib_insync (runs in venv311).
- Socket-Only Mode (--socket-only): Standard library only (no external dependencies),
  for CI/deploy runners.
"""

import sys
import socket
import argparse
import logging
import time
from typing import Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("smoke_test")


def test_tcp_socket(host: str, port: int, timeout: float = 3.0) -> Tuple[bool, str]:
    """Test raw TCP connection using standard library socket (zero dependencies)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return True, f"Connection to {host}:{port} succeeded."
        else:
            return False, f"Connection to {host}:{port} failed (errno={result})."
    except socket.gaierror as e:
        return False, f"Host resolution failed for '{host}': {e}"
    except Exception as e:
        return False, f"Exception connecting to {host}:{port}: {e}"


def run_socket_checks(
    live_host: str,
    live_port: int,
    paper_host: str,
    paper_port: int,
    timeout: float
) -> Tuple[bool, bool]:
    """Run non-destructive TCP probes on broker ports. Returns (live_ok, paper_ok)."""
    logger.info("=" * 70)
    logger.info("STEP 1: TCP Port Probing (Standard Library / Dependency-Free)")
    logger.info("=" * 70)

    # Check live port
    ok_live, msg_live = test_tcp_socket(live_host, live_port, timeout)
    status_live = '[PASS]' if ok_live else '[FAIL]'
    logger.info(f"Live API Socket ({live_host}:{live_port}): {status_live} - {msg_live}")

    # Check paper port
    ok_paper, msg_paper = test_tcp_socket(paper_host, paper_port, timeout)
    status_paper = '[PASS]' if ok_paper else '[FAIL]'
    logger.info(f"Paper API Socket ({paper_host}:{paper_port}): {status_paper} - {msg_paper}")

    if not ok_live:
        logger.error(f"❌ Live Gateway port {live_port} is not reachable.")
        return False, ok_paper
    if not ok_paper:
        logger.warning(
            f"⚠️ Paper Gateway port {paper_port} is not reachable. (Paper gateway down?)"
        )

    logger.info("✅ TCP socket connectivity confirmed.")
    return True, ok_paper


def _verify_live_account(ib_live, live_port: int) -> bool:
    """
    Assert account is live and strictly starts with 'U'.
    Fails closed on 'DU' (paper) or any non-'U' prefix across ALL returned accounts.
    """
    accounts = ib_live.managedAccounts()
    logger.info(f"Connected to Live Gateway. Managed accounts: {accounts}")

    if not accounts:
        logger.error("❌ No managed accounts returned by Live Gateway.")
        return False

    # Edge case: Verify no account in the list is a paper account ('DU...')
    for acct in accounts:
        if not acct:
            continue
        acct_clean = str(acct).strip().upper()
        if acct_clean.startswith("DU"):
            logger.error(
                f"❌ CRITICAL FATAL ERROR: Live Gateway returned a PAPER account ({acct})!\n"
                f"   Port {live_port} is supposed to route to LIVE TRADING with real capital.\n"
                f"   Check your container configuration in .ibgateway-live.env!"
            )
            return False

    # The primary live account must strictly begin with 'U'
    primary_account = str(accounts[0]).strip().upper()
    if not primary_account.startswith("U"):
        logger.error(
            f"❌ CRITICAL FATAL ERROR: Primary account '{accounts[0]}' does not start with "
            f"standard IBKR live prefix 'U'. Failing closed for real-money safety."
        )
        return False

    logger.info(f"✅ CONFIRMED: Connected to LIVE broker account '{accounts[0]}'.")
    return True


def _verify_market_data(ib_live, stock_cls) -> bool:
    """
    Test live market data quote for SPY.
    Returns True if quote received, False if timed out or invalid.
    """
    logger.info("Testing live market data quote for SPY...")
    try:
        contract = stock_cls("SPY", "SMART", "USD")
        ib_live.qualifyContracts(contract)
        ticker = ib_live.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)

        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            ib_live.sleep(0.5)
            price = ticker.marketPrice() or ticker.last or ticker.close
            if price is not None and price > 0:
                logger.info(f"✅ Live Market Data Feed Verified: SPY quote = ${price:.2f}")
                return True

        logger.error(
            "❌ Could not obtain real-time SPY quote within 5.0 seconds. "
            "Verify IBKR live market data subscriptions for US Equities."
        )
        return False
    except Exception as e:
        logger.error(f"❌ Error requesting market data: {e}")
        return False


def _cross_check_paper_gateway(
    paper_host: str,
    paper_port: int,
    client_id: int,
    timeout: float,
    ib_cls,
    paper_socket_reachable: bool = True
) -> bool:
    """
    Cross-check Paper Gateway isolation.
    Fails closed if paper port returns any live or non-DU account (cross-wire check).
    """
    if not paper_socket_reachable:
        logger.warning(
            f"⚠️ Paper Gateway port {paper_port} was not reachable during socket check; "
            f"skipping paper isolation cross-check."
        )
        return True

    logger.info("-" * 70)
    logger.info(f"Cross-checking Paper Gateway isolation on port {paper_port}...")
    ib_paper = ib_cls()
    try:
        ib_paper.connect(
            paper_host, paper_port, clientId=client_id + 1, timeout=timeout, readonly=True
        )
        if not ib_paper.isConnected():
            logger.error(
                f"❌ Failed to establish connection to Paper Gateway on port {paper_port}."
            )
            return False

        paper_accounts = ib_paper.managedAccounts()
        logger.info(f"Paper Gateway active. Accounts: {paper_accounts}")
        if not paper_accounts:
            logger.error("❌ Paper Gateway returned no accounts.")
            return False

        # Edge case: verify NO account on paper port is a live account
        for acct in paper_accounts:
            if not acct:
                continue
            acct_clean = str(acct).strip().upper()
            if acct_clean.startswith("U") and not acct_clean.startswith("DU"):
                logger.error(
                    f"❌ CRITICAL CROSS-WIRE DETECTED: Paper port {paper_port} returned LIVE "
                    f"account '{acct}'! Paper port must strictly route to DU... accounts."
                )
                return False

        primary_paper = str(paper_accounts[0]).strip().upper()
        if not primary_paper.startswith("DU"):
            logger.error(
                f"❌ CRITICAL CROSS-WIRE DETECTED: Paper port {paper_port} primary account "
                f"'{paper_accounts[0]}' does NOT start with 'DU'!"
            )
            return False

        logger.info(
            f"✅ CONFIRMED: Paper Gateway correctly routes to paper account "
            f"'{paper_accounts[0]}'."
        )
        logger.info(
            "✅ Zero cross-interference: both gateways operate concurrently in correct modes."
        )
        return True
    except Exception as e:
        logger.error(f"❌ Error during Paper Gateway isolation cross-check: {e}")
        return False
    finally:
        if ib_paper.isConnected():
            ib_paper.disconnect()


def run_broker_handshake(
    live_host: str,
    live_port: int,
    paper_host: str,
    paper_port: int,
    client_id: int,
    skip_market_data: bool = False,
    timeout: float = 10.0,
    paper_socket_reachable: bool = True
) -> bool:
    """Run full read-only broker handshake via ib_insync."""
    logger.info("=" * 70)
    logger.info("STEP 2: Full Broker Handshake & Live Account Verification")
    logger.info("=" * 70)

    try:
        from ib_insync import IB, Stock, util
    except ImportError:
        logger.error(
            "❌ 'ib_insync' is not installed in the current Python environment.\n"
            "   Run this script with the trading venv Python interpreter:\n"
            "   /home/i030983/venv311/bin/python3 scripts/smoke-test-live-gateway.py\n"
            "   Or use --socket-only for dependency-free TCP probing."
        )
        return False

    util.patchAsyncio()
    ib_live = IB()

    try:
        logger.info(
            f"Connecting read-only client (CID {client_id}) to Live Gateway "
            f"{live_host}:{live_port}..."
        )
        ib_live.connect(
            live_host, live_port, clientId=client_id, timeout=timeout, readonly=True
        )

        if not ib_live.isConnected():
            logger.error("❌ Failed to establish API connection to Live Gateway.")
            return False

        if not _verify_live_account(ib_live, live_port):
            return False

        if not skip_market_data:
            if not _verify_market_data(ib_live, Stock):
                logger.error("❌ Live market data subscription verification failed.")
                return False
        else:
            logger.warning("⚠️ Market data check skipped via --skip-market-data.")

    except Exception as e:
        logger.error(f"❌ Exception during Live Broker Handshake: {e}")
        return False
    finally:
        if ib_live.isConnected():
            ib_live.disconnect()
            logger.info("Disconnected read-only client from Live Gateway.")

    # Cross-check Paper Gateway isolation
    paper_ok = _cross_check_paper_gateway(
        paper_host=paper_host,
        paper_port=paper_port,
        client_id=client_id,
        timeout=timeout,
        ib_cls=IB,
        paper_socket_reachable=paper_socket_reachable
    )
    if not paper_ok:
        logger.error("❌ Paper Gateway isolation cross-check failed.")
        return False

    logger.info("=" * 70)
    if skip_market_data:
        logger.info(
            "⚠️ GATE CONDITIONALLY PASSED: Live connection & isolation verified "
            "(market data NOT verified due to --skip-market-data)."
        )
    else:
        logger.info(
            "🎉 MANDATORY GATE PASSED: Live Gateway is fully operational and verified."
        )
    logger.info("=" * 70)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Zero-Size Live Broker Smoke Test (Phase 1 Gate)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host IP (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=4001, help="Live Gateway API port (default: 4001)"
    )
    parser.add_argument(
        "--paper-port", type=int, default=4002, help="Paper Gateway API port (default: 4002)"
    )
    parser.add_argument(
        "--client-id", type=int, default=99, help="Verification Client ID (default: 99)"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Connection timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "--socket-only", action="store_true", help="Run only dependency-free TCP socket probe"
    )
    parser.add_argument(
        "--skip-market-data", action="store_true",
        help="Skip market data quote subscription check"
    )

    args = parser.parse_args()

    # Step 1: TCP Socket Probe (Always run, dependency-free)
    live_socket_ok, paper_socket_ok = run_socket_checks(
        live_host=args.host,
        live_port=args.port,
        paper_host=args.host,
        paper_port=args.paper_port,
        timeout=min(args.timeout, 3.0)
    )

    if not live_socket_ok:
        sys.exit(1)

    if args.socket_only:
        logger.info("Socket-only mode requested; skipping broker handshake.")
        sys.exit(0)

    # Step 2: Full Broker Handshake
    broker_ok = run_broker_handshake(
        live_host=args.host,
        live_port=args.port,
        paper_host=args.host,
        paper_port=args.paper_port,
        client_id=args.client_id,
        skip_market_data=args.skip_market_data,
        timeout=args.timeout,
        paper_socket_reachable=paper_socket_ok
    )

    sys.exit(0 if broker_ok else 1)


if __name__ == "__main__":
    main()

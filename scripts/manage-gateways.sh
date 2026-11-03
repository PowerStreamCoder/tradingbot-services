#!/bin/bash
#
# Operational management tool for Dual Interactive Brokers Gateways (Phase 1).
#
# Codifies container execution so gateway management is reproducible, versioned
# in Git, and enforces strict memory limits (--memory=512m, -Xmx384m) to protect
# the 4 GB VM from OOM/swap saturation.
#
# Usage:
#   manage-gateways.sh status
#   manage-gateways.sh cap-paper
#   manage-gateways.sh start-live
#   manage-gateways.sh stop-live
#   manage-gateways.sh verify-ports
#

set -uo pipefail

IMAGE="${IB_GATEWAY_IMAGE:-ghcr.io/gnzsnz/ib-gateway:stable}"
PAPER_ENV="/home/i030983/.ibgateway.env"
LIVE_ENV="/home/i030983/.ibgateway-live.env"
MEM_LIMIT="512m"
JAVA_OPTS="-Xmx384m"

# Check if a TCP port is listening locally (portable across Linux ss and macOS/BSD lsof/netstat)
is_port_listening() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | grep -qE "[:.]${port}[[:space:]]"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -qE "[:.]${port}[[:space:]].*LISTEN"
    else
        return 1
    fi
}

status() {
    echo "========================================================================"
    echo "Broker Gateways Status (Docker & Ports)"
    echo "========================================================================"
    
    echo -e "\n1. Docker Containers:"
    docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null | grep -E "NAMES|ib-gateway" || echo "No ib-gateway containers found."
    
    echo -e "\n2. Container Memory Usage:"
    docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" $(docker ps -q --filter "name=ib-gateway" 2>/dev/null) 2>/dev/null || echo "No running ib-gateway containers."

    echo -e "\n3. Listening API Ports (Loopback Check):"
    echo "Port 4002 (Paper API):   $(is_port_listening 4002 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "Port 4004 (Paper Socat): $(is_port_listening 4004 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "Port 4001 (Live API):    $(is_port_listening 4001 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "Port 4003 (Live Socat):  $(is_port_listening 4003 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "Port 5900 (Paper VNC):   $(is_port_listening 5900 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "Port 5901 (Live VNC):    $(is_port_listening 5901 && echo 'LISTENING [OK]' || echo 'DOWN')"
    echo "========================================================================"
}

cap_paper() {
    echo "Applying memory cap (--memory=${MEM_LIMIT}) to existing Paper Gateway..."
    if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway"; then
        echo "Error: Container 'ib-gateway' not found."
        exit 1
    fi

    # Check if docker update can apply it live without restart
    echo "Updating container resource limits..."
    if docker update --memory "${MEM_LIMIT}" --memory-swap "${MEM_LIMIT}" ib-gateway 2>/dev/null; then
        echo "✅ Memory limit ${MEM_LIMIT} applied successfully to ib-gateway via docker update."
    else
        echo "Live update failed; re-creating container with memory limit..."
        if [ ! -f "$PAPER_ENV" ]; then
            echo "Error: $PAPER_ENV not found."
            exit 1
        fi
        docker stop ib-gateway 2>/dev/null || true
        docker rm ib-gateway 2>/dev/null || true
        docker run -d \
            --name ib-gateway \
            --restart unless-stopped \
            --memory="${MEM_LIMIT}" \
            --env-file "$PAPER_ENV" \
            -e TRADING_MODE=paper \
            -e TWS_PORT=4002 \
            -e JAVA_OPTS="${JAVA_OPTS}" \
            -p 127.0.0.1:4002:4002 \
            -p 127.0.0.1:4004:4004 \
            -p 127.0.0.1:5900:5900 \
            "$IMAGE"
        echo "✅ Recreated ib-gateway with ${MEM_LIMIT} limit."
    fi
}

start_live() {
    echo "Provisioning and starting Live Gateway (ib-gateway-live)..."
    if [ ! -f "$LIVE_ENV" ]; then
        echo "❌ Error: Secrets file '$LIVE_ENV' does not exist."
        echo "Please create '$LIVE_ENV' with live IBKR credentials before starting live gateway."
        exit 1
    fi

    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway-live"; then
        echo "Found existing ib-gateway-live container. Removing old instance..."
        docker stop ib-gateway-live 2>/dev/null || true
        docker rm ib-gateway-live 2>/dev/null || true
    fi

    echo "Launching ib-gateway-live with --memory=${MEM_LIMIT} on ports 4001, 4003, 5901..."
    docker run -d \
        --name ib-gateway-live \
        --restart unless-stopped \
        --memory="${MEM_LIMIT}" \
        --env-file "$LIVE_ENV" \
        -e TRADING_MODE=live \
        -e TWS_PORT=4001 \
        -e JAVA_OPTS="${JAVA_OPTS}" \
        -p 127.0.0.1:4001:4001 \
        -p 127.0.0.1:4003:4003 \
        -p 127.0.0.1:5901:5901 \
        "$IMAGE"

    echo "✅ ib-gateway-live container launched."
    echo "Checking port 4001 availability (allowing 15s for initialization)..."
    sleep 5
    for i in {1..10}; do
        if is_port_listening 4001; then
            echo "✅ Port 4001 is listening and ready."
            return 0
        fi
        sleep 2
    done
    echo "⚠️ Warning: Port 4001 not listening yet. Check 'docker logs ib-gateway-live'."
}

stop_live() {
    echo "Stopping Live Gateway (ib-gateway-live)..."
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "ib-gateway-live"; then
        docker stop ib-gateway-live
        echo "✅ ib-gateway-live stopped."
    else
        echo "ib-gateway-live is not running."
    fi
}

verify_ports() {
    echo "Verifying local loopback port binding (security check)..."
    for port in 4001 4002 4003 4004 5900 5901; do
        if is_port_listening "${port}"; then
            echo "Port ${port}: OPEN [LISTENING]"
        else
            echo "Port ${port}: CLOSED / NOT LISTENING"
        fi
    done
}

case "${1:-status}" in
    status)
        status
        ;;
    cap-paper)
        cap_paper
        ;;
    start-live)
        start_live
        ;;
    stop-live)
        stop_live
        ;;
    verify-ports)
        verify_ports
        ;;
    *)
        echo "Usage: $0 {status|cap-paper|start-live|stop-live|verify-ports}"
        exit 1
        ;;
esac

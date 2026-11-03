#!/bin/bash
#
# Operational management tool for Dual Interactive Brokers Gateways (Phase 1).
#
# Codifies container execution so gateway management is reproducible, versioned
# in Git, and enforces strict memory limits (--memory=512m, --memory-swap=512m,
# JAVA_HEAP_SIZE=384) to protect the 4 GB VM from OOM/swap saturation.
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
JAVA_HEAP_SIZE="384"

# Check if a TCP port is listening locally
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

# Check if a TCP port is strictly bound to loopback (127.0.0.1)
is_port_loopback_bound() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | grep -qE "(127\.0\.0\.1|\[::1\]):${port}[[:space:]]"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"${port}" -sTCP:LISTEN -n -P 2>/dev/null | grep -qE "127\.0\.0\.1:${port}"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -qE "(127\.0\.0\.1|localhost)[.:]${port}[[:space:]].*LISTEN"
    else
        return 1
    fi
}

port_security_status() {
    local port="$1"
    if ! is_port_listening "$port"; then
        echo "DOWN / NOT LISTENING"
    elif is_port_loopback_bound "$port"; then
        echo "LISTENING [OK: Loopback 127.0.0.1]"
    else
        echo "⚠️ INSECURE: LISTENING ON NON-LOOPBACK INTERFACE!"
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

    echo -e "\n3. Listening API Ports (Loopback & Security Check):"
    echo "Port 4002 (Paper API):   $(port_security_status 4002)"
    echo "Port 4004 (Paper Socat): $(port_security_status 4004)"
    echo "Port 4001 (Live API):    $(port_security_status 4001)"
    echo "Port 4003 (Live Socat):  $(port_security_status 4003)"
    echo "Port 5900 (Paper VNC):   $(port_security_status 5900)"
    echo "Port 5901 (Live VNC):    $(port_security_status 5901)"
    echo "========================================================================"
}

cap_paper() {
    echo "========================================================================"
    echo "Applying memory cap & JVM ceiling to Paper Gateway (ib-gateway)..."
    echo "NOTE: Controlled restart must be executed outside RTH / during maintenance."
    echo "========================================================================"
    
    if [ ! -f "$PAPER_ENV" ]; then
        echo "❌ Error: Secrets file '$PAPER_ENV' not found."
        exit 1
    fi

    echo "Re-creating ib-gateway with --memory=${MEM_LIMIT}, --memory-swap=${MEM_LIMIT}, and JAVA_HEAP_SIZE=${JAVA_HEAP_SIZE}..."
    docker stop ib-gateway 2>/dev/null || true
    docker rm ib-gateway 2>/dev/null || true
    
    docker run -d \
        --name ib-gateway \
        --restart unless-stopped \
        --memory="${MEM_LIMIT}" \
        --memory-swap="${MEM_LIMIT}" \
        --env-file "$PAPER_ENV" \
        -e TRADING_MODE=paper \
        -e TWS_PORT=4002 \
        -e JAVA_HEAP_SIZE="${JAVA_HEAP_SIZE}" \
        -p 127.0.0.1:4002:4002 \
        -p 127.0.0.1:4004:4004 \
        -p 127.0.0.1:5900:5900 \
        "$IMAGE"

    echo "✅ Recreated ib-gateway with ${MEM_LIMIT} memory/swap limits and JAVA_HEAP_SIZE=${JAVA_HEAP_SIZE}."
    echo "Waiting for Paper API port 4002 to listen..."
    for i in {1..15}; do
        if is_port_listening 4002; then
            echo "✅ Paper Gateway port 4002 is listening and verified."
            return 0
        fi
        sleep 2
    done
    echo "⚠️ Warning: Port 4002 not listening yet. Check 'docker logs ib-gateway'."
}

start_live() {
    echo "========================================================================"
    echo "Provisioning and starting Live Gateway (ib-gateway-live)..."
    echo "========================================================================"
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

    echo "Launching ib-gateway-live with --memory=${MEM_LIMIT}, --memory-swap=${MEM_LIMIT}, JAVA_HEAP_SIZE=${JAVA_HEAP_SIZE}..."
    docker run -d \
        --name ib-gateway-live \
        --restart unless-stopped \
        --memory="${MEM_LIMIT}" \
        --memory-swap="${MEM_LIMIT}" \
        --env-file "$LIVE_ENV" \
        -e TRADING_MODE=live \
        -e TWS_PORT=4001 \
        -e JAVA_HEAP_SIZE="${JAVA_HEAP_SIZE}" \
        -p 127.0.0.1:4001:4001 \
        -p 127.0.0.1:4003:4003 \
        -p 127.0.0.1:5901:5901 \
        "$IMAGE"

    echo "✅ ib-gateway-live container launched."
    echo "Checking port 4001 availability (allowing up to 30s for initialization)..."
    sleep 5
    for i in {1..12}; do
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
    echo "Verifying local loopback port binding (strict security check)..."
    local all_ok=0
    for port in 4001 4002 4003 4004 5900 5901; do
        if ! is_port_listening "${port}"; then
            echo "Port ${port}: CLOSED / NOT LISTENING"
        elif is_port_loopback_bound "${port}"; then
            echo "Port ${port}: SECURE [Loopback 127.0.0.1]"
        else
            echo "Port ${port}: ❌ INSECURE [Bound to non-loopback address!]"
            all_ok=1
        fi
    done
    return $all_ok
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

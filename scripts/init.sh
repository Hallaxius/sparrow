#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo " SparroW — Keyless Free LLM Router"
echo "============================================"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found"; exit 1; }

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose not found"
    exit 1
fi

export SPARROW_HOST="${SPARROW_HOST:-0.0.0.0}"
export SPARROW_PORT="${SPARROW_PORT:-8080}"
export SPARROW_ROUTING="${SPARROW_ROUTING:-fair}"
export SPARROW_WARP_ENABLED="${SPARROW_WARP_ENABLED:-true}"
export SPARROW_WARP_URL="${SPARROW_WARP_URL:-socks5://warp:1080}"

echo ""
echo "Starting SparroW + WARP..."
echo "  WARP:     ${SPARROW_WARP_ENABLED}"
echo "  Routing:  ${SPARROW_ROUTING}"
echo "  Port:     ${SPARROW_PORT}"
echo ""

cd "$PROJECT_DIR"
docker compose up -d --build

echo ""
echo "Waiting for WARP to connect (~60-90s)..."
echo ""

MAX_WAIT=180
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf "http://localhost:${SPARROW_PORT}/healthz" >/dev/null 2>&1; then
        echo "SparroW is running!"
        echo ""
        curl -s "http://localhost:${SPARROW_PORT}/healthz" | python3 -m json.tool 2>/dev/null || true
        exit 0
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    echo "  ...waiting (${WAITED}s)"
done

echo ""
echo "ERROR: SparroW did not respond after ${MAX_WAIT}s"
echo "Check logs: docker compose logs sparrow"
exit 1

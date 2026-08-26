#!/bin/bash
set -euo pipefail

echo "Starting SparroW..."

APP_DIR="${SPARROW_APP_DIR:-/app}"
CONFIG_FILE="$APP_DIR/providers.json"
MODELS_FILE="$APP_DIR/models.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "providers.json configuration file not found: $CONFIG_FILE" >&2
    echo "Create it or run 'sparrow init' explicitly before starting the server." >&2
    exit 1
fi

if [ ! -f "$MODELS_FILE" ]; then
    echo "models.json configuration file not found: $MODELS_FILE" >&2
    echo "Create it or run 'sparrow init' explicitly before starting the server." >&2
    exit 1
fi

if [ -z "${SPARROW_API_KEY:-}" ]; then
    echo "SPARROW_API_KEY is required; set it before starting the server." >&2
    exit 1
fi

echo "Using JSON configuration: $CONFIG_FILE"
exec uv run python -m sparrow

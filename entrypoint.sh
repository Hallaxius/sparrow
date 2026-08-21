#!/bin/bash
set -euo pipefail

echo "Starting SparroW..."

# Check if providers.toml exists, if not run sparrow init to generate it
if [ ! -f /app/providers.toml ]; then
    echo "providers.toml not found, running sparrow init to fetch models from providers..."
    uv run python -m sparrow init
else
    echo "providers.toml found, skipping init"
fi

# Start the server
exec uv run python -m sparrow
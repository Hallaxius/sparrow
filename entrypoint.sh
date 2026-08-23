#!/bin/bash
set -euo pipefail

echo "Starting SparroW..."

# Check if providers.json exists, if not run sparrow init to generate it
if [ ! -f /app/providers.json ] || [ ! -f /app/models.json ]; then
    echo "JSON config not found, running sparrow init to fetch models from providers..."
    uv run python -m sparrow init
else
    echo "providers.json and models.json found, skipping init"
fi

# Start the server
exec uv run python -m sparrow

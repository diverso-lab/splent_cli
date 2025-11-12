#!/bin/bash
set -e

# --- Global Git fix (system-level, not global) ---
# Runs every time the container starts
git config --system --add safe.directory /workspace 2>/dev/null || true
for dir in /workspace/*; do
  [ -d "$dir" ] && git config --system --add safe.directory "$dir" 2>/dev/null || true
done
echo "✅ Git safe.directory configured (system-level)."
# -------------------------------------------------------

# If no command is provided, keep the container alive
if [ $# -eq 0 ]; then
    echo "🚀 No command provided. Keeping container alive..."
    exec tail -f /dev/null
else
    # Run the CLI with the provided arguments
    exec splent "$@"
fi

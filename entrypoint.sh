#!/bin/bash
set -e

# Drop privileges to the container user (passed by Docker) BEFORE running splent
if [ "$(id -u)" = "0" ]; then
    # runuser -u splent -- "$0" "$@"
    exec su -s /bin/bash splent -c "$0 $*"
fi

# --- Global Git fix ---
git config --global --add safe.directory /workspace 2>/dev/null || true
for dir in /workspace/*; do
  [ -d "$dir" ] && git config --global --add safe.directory "$dir" 2>/dev/null || true
done
echo "Git safe.directory configured."

# Run SPLENT normally
if [ $# -eq 0 ]; then
    exec tail -f /dev/null
else
    exec splent "$@"
fi

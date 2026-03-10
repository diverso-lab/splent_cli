#!/usr/bin/env bash
set -e

USER_NAME="splent"
USER_UID="${HOST_UID:-1000}"
USER_GID="${HOST_GID:-1000}"
SOCKET="/var/run/docker.sock"

echo "→ Preparing runtime user..."

CURRENT_UID="$(id -u "$USER_NAME")"
CURRENT_GID="$(id -g "$USER_NAME")"

# ------------------------------------------------------------
# Remap UID safely
# ------------------------------------------------------------
if [ "$CURRENT_UID" != "$USER_UID" ]; then
    usermod -u "$USER_UID" "$USER_NAME" 2>/dev/null || true
fi

# ------------------------------------------------------------
# Remap primary GID only if safe
# If HOST_GID already belongs to another group, do not force it.
# This avoids collisions on macOS (e.g. GID 20 -> dialout).
# ------------------------------------------------------------
EXISTING_GROUP_NAME="$(getent group "$USER_GID" | cut -d: -f1 || true)"

if [ -n "$EXISTING_GROUP_NAME" ]; then
    if [ "$EXISTING_GROUP_NAME" = "$USER_NAME" ]; then
        if [ "$CURRENT_GID" != "$USER_GID" ]; then
            usermod -g "$USER_GID" "$USER_NAME" 2>/dev/null || true
        fi
    else
        echo "→ HOST_GID $USER_GID already exists as group '$EXISTING_GROUP_NAME'; keeping primary group unchanged"
    fi
else
    if [ "$CURRENT_GID" != "$USER_GID" ]; then
        groupmod -g "$USER_GID" "$USER_NAME" 2>/dev/null || true
        usermod -g "$USER_GID" "$USER_NAME" 2>/dev/null || true
    fi
fi

# Refresh current ids after possible remap
CURRENT_UID="$(id -u "$USER_NAME")"
CURRENT_GID="$(id -g "$USER_NAME")"

mkdir -p /home/"$USER_NAME"/.ssh
touch /home/"$USER_NAME"/.gitconfig || true
chown -R "$CURRENT_UID":"$CURRENT_GID" /home/"$USER_NAME" /workspace 2>/dev/null || true

# ------------------------------------------------------------
# Docker socket supplementary group
# ------------------------------------------------------------
if [ -S "$SOCKET" ]; then
    echo "→ Docker socket detected"

    SOCKET_GID="$(stat -c %g "$SOCKET" 2>/dev/null || stat -f %g "$SOCKET" 2>/dev/null || true)"

    if [ -n "$SOCKET_GID" ]; then
        echo "→ Docker socket GID: $SOCKET_GID"

        SOCKET_GROUP="$(getent group "$SOCKET_GID" | cut -d: -f1 || true)"

        if [ -z "$SOCKET_GROUP" ]; then
            SOCKET_GROUP="dockerhost"
            groupadd -f -g "$SOCKET_GID" "$SOCKET_GROUP" 2>/dev/null || true
        fi

        usermod -aG "$SOCKET_GROUP" "$USER_NAME" 2>/dev/null || true
        echo "→ Added $USER_NAME to group $SOCKET_GROUP"
    fi
fi

runuser -u "$USER_NAME" -- git config --global --add safe.directory /workspace 2>/dev/null || true
for dir in /workspace/*; do
    [ -d "$dir" ] && runuser -u "$USER_NAME" -- git config --global --add safe.directory "$dir" 2>/dev/null || true
done
echo "Git safe.directory configured."

echo "→ Container ready. Running as $USER_NAME"
exec runuser -u "$USER_NAME" -- tail -f /dev/null
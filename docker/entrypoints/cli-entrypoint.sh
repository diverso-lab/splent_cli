#!/usr/bin/env bash
set -e

USER_NAME="splent"
USER_UID="${HOST_UID:-1000}"
USER_GID="${HOST_GID:-1000}"
SOCKET="/var/run/docker.sock"

echo "→ Preparing runtime user..."

CURRENT_UID="$(id -u "$USER_NAME")"
CURRENT_GID="$(id -g "$USER_NAME")"

if [ "$CURRENT_GID" != "$USER_GID" ]; then
    groupmod -g "$USER_GID" "$USER_NAME" 2>/dev/null || true
fi

if [ "$CURRENT_UID" != "$USER_UID" ]; then
    usermod -u "$USER_UID" -g "$USER_GID" "$USER_NAME" 2>/dev/null || true
fi

mkdir -p /home/"$USER_NAME"/.ssh
touch /home/"$USER_NAME"/.gitconfig || true
chown -R "$USER_UID":"$USER_GID" /home/"$USER_NAME" /workspace 2>/dev/null || true

if [ -S "$SOCKET" ]; then
    echo "→ Docker socket detected"

    SOCKET_GID="$(stat -c %g "$SOCKET" 2>/dev/null || stat -f %g "$SOCKET" 2>/dev/null || echo "")"

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

su - "$USER_NAME" -c 'git config --global --add safe.directory /workspace 2>/dev/null || true'
for dir in /workspace/*; do
    if [ -d "$dir" ]; then
        su - "$USER_NAME" -c "git config --global --add safe.directory '$dir' 2>/dev/null || true"
    fi
done
echo "Git safe.directory configured."

if [ $# -eq 0 ]; then
    exec su - "$USER_NAME"
else
    exec su - "$USER_NAME" -c "splent $*"
fi
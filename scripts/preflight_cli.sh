#!/usr/bin/env bash
#
# preflight_cli.sh — Host-side pre-flight checks for the SPLENT CLI.
#
# Runs on the HOST (not inside the container) before `make cli` / `make setup`
# enter or build the CLI container. Catches the common, costly, hard-to-debug
# failures early and prints actionable guidance instead of cryptic Docker errors.
#
# Checks:
#   1. Docker is installed.
#   2. The Docker daemon is running.
#   3. Docker Compose v2 (`docker compose`) is available (legacy `docker-compose`
#      is accepted with a warning).
#   4. ~/.gitconfig and ~/.ssh are in a sane state for bind-mounting (warn only).
#   5. (optional) The CLI container exists and is running — auto-starts it if it
#      is merely stopped; tells the user to run `make setup` if it is missing.
#
# Usage:
#   preflight_cli.sh                      # host checks only (1-4)
#   preflight_cli.sh <container_name>     # host checks + container check (5)
#
# Exit code: 0 if safe to continue, 1 if a hard failure was found.

set -uo pipefail

CONTAINER_NAME="${1:-}"

# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET="\033[0m"; C_RED="\033[31m"; C_GREEN="\033[32m"
    C_YELLOW="\033[33m"; C_CYAN="\033[36m"; C_BOLD="\033[1m"
else
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""
fi

ok()   { printf "  ${C_GREEN}[✔]${C_RESET} %b\n" "$1"; }
warn() { printf "  ${C_YELLOW}[⚠]${C_RESET} %b\n" "$1"; }
fail() { printf "  ${C_RED}[✖]${C_RESET} %b\n" "$1"; }
hint() { printf "      ${C_CYAN}%b${C_RESET}\n" "$1"; }

FAILED=0

printf "\n${C_BOLD}🩺 SPLENT CLI pre-flight${C_RESET}\n\n"

# ----------------------------------------------------------------------------
# 1. Docker installed
# ----------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    ok "Docker installed — $(docker --version 2>/dev/null)"
else
    fail "Docker is not installed."
    hint "Install Docker Desktop (macOS/Windows) or Docker Engine (Linux):"
    hint "https://www.docker.com/products/docker-desktop/"
    FAILED=1
fi

# ----------------------------------------------------------------------------
# 2. Docker daemon running and reachable WITHOUT sudo
#
# Distinguishes two failure modes without ever prompting for a password:
#   - "permission denied" → daemon is up but the user is not in the docker group
#   - anything else        → daemon is not running
# ----------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        ok "Docker daemon is running (reachable without sudo)"
    else
        daemon_err="$(docker info 2>&1 >/dev/null)"
        if printf '%s' "$daemon_err" | grep -qiE 'permission denied|connect: permission'; then
            fail "Docker daemon is up but not reachable without sudo (permission denied)."
            hint "Add your user to the 'docker' group, then re-login:"
            hint "sudo usermod -aG docker \$USER && newgrp docker"
            hint "Do NOT run SPLENT with sudo — fix the group instead."
        else
            fail "Docker is installed but the daemon is not responding."
            hint "Start it — Docker Desktop, or on Linux: sudo systemctl start docker"
        fi
        FAILED=1
    fi
fi

# ----------------------------------------------------------------------------
# 3. Docker Compose v2 available
# ----------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
        ok "Docker Compose v2 available — $(docker compose version --short 2>/dev/null)"
    elif command -v docker-compose >/dev/null 2>&1; then
        warn "Only legacy 'docker-compose' found — upgrade to Compose v2 (the 'docker compose' plugin)."
        hint "https://docs.docker.com/compose/install/"
    else
        fail "Docker Compose is not available ('docker compose' plugin missing)."
        hint "Install the Compose v2 plugin: https://docs.docker.com/compose/install/"
        FAILED=1
    fi
fi

# ----------------------------------------------------------------------------
# 4. Git config / SSH sanity for bind-mounts (warn only)
#
# docker-compose.yml bind-mounts ${HOME}/.gitconfig and ${HOME}/.ssh into the
# container. If either does NOT exist on the host, Docker silently creates it as
# an empty *directory*, which breaks git (.gitconfig as a dir) and SSH (no keys)
# INSIDE the container — the usual cause of "feature:clone" failures.
# ----------------------------------------------------------------------------
GITCONFIG="${HOME}/.gitconfig"
SSH_DIR="${HOME}/.ssh"

if [ -d "$GITCONFIG" ]; then
    warn "~/.gitconfig is a DIRECTORY, not a file — git will fail inside the container."
    hint "Fix it on the host:"
    hint "rm -rf ~/.gitconfig && touch ~/.gitconfig"
    hint "git config --global user.name \"Your Name\""
    hint "git config --global user.email \"you@example.com\""
elif [ ! -e "$GITCONFIG" ]; then
    warn "~/.gitconfig does not exist — Docker will mount it as an empty directory and break git."
    hint "Create it before launching the container:"
    hint "touch ~/.gitconfig"
    hint "git config --global user.name \"Your Name\""
    hint "git config --global user.email \"you@example.com\""
else
    name="$(git config --global user.name 2>/dev/null || true)"
    email="$(git config --global user.email 2>/dev/null || true)"
    if [ -z "$name" ] || [ -z "$email" ]; then
        warn "~/.gitconfig exists but user.name/user.email are not set — commits inside the container will fail."
        hint "git config --global user.name \"Your Name\""
        hint "git config --global user.email \"you@example.com\""
    else
        ok "Git identity configured — ${name} <${email}>"
    fi
fi

if [ ! -d "$SSH_DIR" ]; then
    warn "~/.ssh does not exist — SSH clones will be unavailable (CLI will fall back to HTTPS + GITHUB_TOKEN)."
    hint "If you use SSH with GitHub, create your key: ssh-keygen -t ed25519 -C \"you@example.com\""
else
    ok "~/.ssh present"
fi

# ----------------------------------------------------------------------------
# 5. Container check (optional) — auto-start if stopped
# ----------------------------------------------------------------------------
if [ -n "$CONTAINER_NAME" ] && [ "$FAILED" -eq 0 ]; then
    state="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)"
    if [ -z "$state" ]; then
        fail "Container '${CONTAINER_NAME}' does not exist yet."
        hint "Build and start it with: make setup"
        FAILED=1
    else
        # Warn if the container is bound to a different workspace than this one.
        expected_ws="$(cd .. 2>/dev/null && pwd || true)"
        mount_src="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)"
        if [ -n "$expected_ws" ] && [ -n "$mount_src" ] && [ "$mount_src" != "$expected_ws" ]; then
            warn "Container is bound to another workspace:"
            hint "  mounted : ${mount_src}"
            hint "  current : ${expected_ws}"
            hint "Re-bind it to this workspace with: make docker-up"
        fi

        running="$state"
        if [ "$state" = "true" ]; then
            ok "Container '${CONTAINER_NAME}' is running"
        else
            warn "Container '${CONTAINER_NAME}' is stopped — starting it..."
            if docker start "$CONTAINER_NAME" >/dev/null 2>&1; then
                ok "Container '${CONTAINER_NAME}' started"
                running="true"
            else
                fail "Could not start container '${CONTAINER_NAME}'."
                hint "Recreate it with: make docker-up   (or rebuild: make setup-rebuild)"
                FAILED=1
            fi
        fi

        # Workspace .env presence — the CLI reads /workspace/.env (load_dotenv)
        # for credentials. Missing/empty .env is a silent source of clone/auth
        # failures, so warn before dropping into the shell.
        if [ -n "$expected_ws" ] && [ ! -f "${expected_ws}/.env" ]; then
            warn "No .env found at ${expected_ws}/.env — SPLENT commands will run without credentials."
            hint "Create it with: make setup   (copies .env.example), then fill in your tokens."
        fi

        # Docker socket must be bind-mounted INTO the container — the 'make cli'
        # exec line stat()s /var/run/docker.sock; without it the shell fails and
        # docker-in-docker commands (product:up, etc.) cannot work.
        if [ "$running" = "true" ]; then
            if docker exec "$CONTAINER_NAME" test -S /var/run/docker.sock >/dev/null 2>&1; then
                ok "Docker socket mounted inside the container"
            else
                fail "Docker socket is not mounted inside '${CONTAINER_NAME}'."
                hint "Recreate the container so the socket bind-mount applies: make docker-up"
                FAILED=1
            fi
        fi
    fi
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
echo
if [ "$FAILED" -ne 0 ]; then
    printf "${C_RED}${C_BOLD}❌ Pre-flight failed — resolve the items above before continuing.${C_RESET}\n\n"
    exit 1
fi
printf "${C_GREEN}${C_BOLD}✅ Pre-flight passed.${C_RESET}\n\n"
exit 0

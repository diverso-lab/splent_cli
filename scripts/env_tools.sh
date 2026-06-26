#!/usr/bin/env bash
#
# env_tools.sh — Host-side .env management for the SPLENT CLI workspace.
#
# The workspace `.env` (one level ABOVE splent_cli/) drives two things:
#   - the Docker bind-mount  (SPLENT_HOST_PROJECT_DIR → /workspace)
#   - the in-container environment (WORKING_DIR, SPLENT_APP, credentials, …)
#
# These helpers keep that file sane WITHOUT entering the container. They are the
# host-side counterpart of `splent check:env`, which runs INSIDE the container
# and cannot see/repair the host path or rewrite the file.
#
# Commands:
#   check        Validate .env (read-only). Flags:
#                  - duplicate keys           (KEY defined more than once)
#                  - overlapping/unknown keys (not in .env.example)
#                  - malformed lines          (not BLANK / #comment / KEY=value)
#                  - stale host path          (SPLENT_HOST_PROJECT_DIR ≠ real dir)
#                  - dangling product         (SPLENT_APP points to a missing dir)
#                  - missing keys             (present in .env.example, absent here)
#                Exit 1 if any hard problem is found.
#   fix-workdir  Re-point SPLENT_HOST_PROJECT_DIR at the REAL workspace path and
#                ensure WORKING_DIR=/workspace. Idempotent.
#   fix-app      Clear SPLENT_APP when it points to a product that no longer
#                exists on disk (returns to detached mode). Idempotent.
#   fix-env      Run fix-workdir + fix-app, then check.
#
# Usage:
#   env_tools.sh <check|fix-workdir|fix-app|fix-env> [--embed] [--quiet]
#
#   --embed   Print only the check lines (no banner / no summary) so the output
#             blends into a host pre-flight run. Exit code still reflects hard
#             problems. Used by preflight_cli.sh — single source of truth.
#   --quiet   Suppress passing [✔] lines (warnings/errors still print).
#
# Exit code: 0 = OK / nothing to fix, 1 = hard problem found (check / fix-env).

set -uo pipefail

# ----------------------------------------------------------------------------
# Arg parsing — first non-flag token is the command.
# ----------------------------------------------------------------------------
CMD=""
EMBED=0
QUIET=0
for a in "$@"; do
    case "$a" in
        --embed) EMBED=1 ;;
        --quiet) QUIET=1 ;;
        --*)     ;;            # ignore unknown flags
        *)       [ -z "$CMD" ] && CMD="$a" ;;
    esac
done
CMD="${CMD:-check}"

# In --quiet mode, passing [✔] lines are muted; warnings/errors and repair
# actions still print (see act/warn/fail).
SUPPRESS_OK="$QUIET"

# ----------------------------------------------------------------------------
# Paths — resolved relative to THIS script so it works from any CWD.
#   splent_cli/scripts/env_tools.sh  →  ../..  =  workspace root
# ----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$WORKSPACE_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/../.env.example"

# Inside a Vagrant box the workspace is always mounted at /workspace, so the
# host path injected into .env must be /workspace rather than the real dir.
if [ -d "$WORKSPACE_DIR/.vagrant" ]; then
    EXPECTED_HOST_DIR="/workspace"
else
    EXPECTED_HOST_DIR="$WORKSPACE_DIR"
fi

# ----------------------------------------------------------------------------
# Output helpers (same vocabulary as preflight_cli.sh)
# ----------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET="\033[0m"; C_RED="\033[31m"; C_GREEN="\033[32m"
    C_YELLOW="\033[33m"; C_CYAN="\033[36m"; C_BOLD="\033[1m"
else
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""
fi

# ok()  passing check — muted by --quiet (already-seen / nothing-to-do noise).
# act() a repair actually happened — ALWAYS prints, even under --quiet.
# warn()/fail() problems — always print.
ok()   { [ "${SUPPRESS_OK:-0}" = "1" ] && return 0; printf "  ${C_GREEN}[✔]${C_RESET} %b\n" "$1"; }
act()  { printf "  ${C_GREEN}[✔]${C_RESET} %b\n" "$1"; }
warn() { printf "  ${C_YELLOW}[⚠]${C_RESET} %b\n" "$1"; }
fail() { printf "  ${C_RED}[✖]${C_RESET} %b\n" "$1"; }
hint() { printf "      ${C_CYAN}%b${C_RESET}\n" "$1"; }
info() { printf "  ${C_CYAN}•${C_RESET} %b\n" "$1"; }

FAILED=0
WARNED=0

# ----------------------------------------------------------------------------
# .env primitives
# ----------------------------------------------------------------------------

# Read the value of KEY from .env (first match), empty string if absent.
env_get() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 0
    local line
    line="$(grep -m1 -E "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null || true)"
    printf '%s' "${line#*=}"
}

# Count how many times KEY is defined in .env (0 if absent / no file).
env_count() {
    local key="$1"
    [ -f "$ENV_FILE" ] || { printf '0'; return 0; }
    grep -cE "^[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null || printf '0'
}

# Insert-or-replace KEY=VALUE in .env (no sed: values may contain slashes).
# If the key was accidentally defined more than once, the duplicates are
# collapsed into the single (first) rewritten line.
env_upsert() {
    local key="$1" val="$2"
    local tmp found=0
    tmp="$(mktemp)"
    if [ -f "$ENV_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                "$key="*)
                    if [ "$found" -eq 0 ]; then
                        printf '%s=%s\n' "$key" "$val"
                        found=1
                    fi ;;                       # drop further duplicates
                *) printf '%s\n' "$line" ;;
            esac
        done < "$ENV_FILE" > "$tmp"
    fi
    [ "$found" -eq 0 ] && printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
}

# Delete every line defining KEY.
env_delete() {
    local key="$1" tmp
    tmp="$(mktemp)"
    grep -v -E "^[[:space:]]*${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
    mv "$tmp" "$ENV_FILE"
}

# Copy the template if .env does not exist yet (shared by every fix command).
ensure_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$ENV_EXAMPLE" ]; then
            cp "$ENV_EXAMPLE" "$ENV_FILE"
            info "Created .env from .env.example"
        else
            : > "$ENV_FILE"
            info "Created empty .env"
        fi
    fi
}

# ----------------------------------------------------------------------------
# check
# ----------------------------------------------------------------------------
do_check() {
    [ "$EMBED" = 1 ] || printf "\n${C_BOLD}🩺 SPLENT .env check${C_RESET}  (%s)\n\n" "$ENV_FILE"

    if [ ! -f "$ENV_FILE" ]; then
        fail ".env not found at ${ENV_FILE}"
        hint "Create it with: make env-prepare"
        FAILED=1
        summary; return
    fi

    # 1. Duplicate keys -------------------------------------------------------
    local dupes
    dupes="$(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" \
        | sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/' \
        | sort | uniq -d)"
    if [ -n "$dupes" ]; then
        while IFS= read -r k; do
            [ -z "$k" ] && continue
            local lines
            lines="$(grep -nE "^[[:space:]]*${k}=" "$ENV_FILE" | cut -d: -f1 | paste -sd, -)"
            fail "Duplicate variable '${k}' (lines ${lines}) — last one silently wins."
            FAILED=1
        done <<< "$dupes"
        hint "Remove the redundant line(s); keep a single definition per key."
    else
        ok "No duplicate variables"
    fi

    # 2. Malformed lines ------------------------------------------------------
    local malformed=""
    local n=0
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        case "$line" in
            ""|"#"*) ;;                                   # blank / comment
            *=*)
                case "$line" in
                    [A-Za-z_]*=*) ;;                      # valid KEY=value
                    *) malformed="${malformed}${n} " ;;   # starts oddly
                esac ;;
            *) malformed="${malformed}${n} " ;;           # no '=' at all
        esac
    done < "$ENV_FILE"
    if [ -n "$malformed" ]; then
        fail "Malformed line(s): ${malformed%% }"
        hint "Each entry must be a comment (#…), blank, or KEY=value."
        FAILED=1
    else
        ok "No malformed lines"
    fi

    # 3. Host path (SPLENT_HOST_PROJECT_DIR) ----------------------------------
    local host_dir; host_dir="$(env_get SPLENT_HOST_PROJECT_DIR)"
    if [ -z "$host_dir" ]; then
        warn "SPLENT_HOST_PROJECT_DIR is not set — the bind-mount source is unknown."
        hint "Fix it with: make fix-workdir"
        WARNED=1
    elif [ "$host_dir" != "$EXPECTED_HOST_DIR" ]; then
        warn "SPLENT_HOST_PROJECT_DIR is stale (breaks product bind-mounts):"
        hint "  in .env  : ${host_dir}"
        hint "  expected : ${EXPECTED_HOST_DIR}"
        hint "Repair it with: make fix-workdir"
        WARNED=1
    else
        ok "SPLENT_HOST_PROJECT_DIR = ${host_dir}"
    fi

    # 4. WORKING_DIR ----------------------------------------------------------
    local wd; wd="$(env_get WORKING_DIR)"
    if [ -z "$wd" ]; then
        warn "WORKING_DIR is not set (container expects /workspace)."
        hint "Fix it with: make fix-workdir"
        WARNED=1
    elif [ "$wd" != "/workspace" ]; then
        warn "WORKING_DIR = ${wd} (expected /workspace inside the container)."
        WARNED=1
    else
        ok "WORKING_DIR = ${wd}"
    fi

    # 5. SPLENT_APP (active product) -----------------------------------------
    local app; app="$(env_get SPLENT_APP)"
    if [ -z "$app" ]; then
        ok "SPLENT_APP unset (detached mode)"
    elif [ -d "$WORKSPACE_DIR/$app" ]; then
        ok "SPLENT_APP = ${app}"
    else
        warn "SPLENT_APP = ${app} but '${WORKSPACE_DIR}/${app}' does not exist (dangling)."
        hint "Repair it with: make fix-app   (returns to detached mode)"
        WARNED=1
    fi

    # 6. Drift vs .env.example -----------------------------------------------
    if [ -f "$ENV_EXAMPLE" ]; then
        local example_keys env_keys missing unknown
        example_keys="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_EXAMPLE" | cut -d= -f1 | sort -u)"
        env_keys="$(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" \
            | sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/' | sort -u)"

        # Keys the template defines that this .env is missing.
        missing="$(comm -23 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$env_keys"))"
        if [ -n "$missing" ]; then
            warn "Keys present in .env.example but missing here: $(printf '%s' "$missing" | paste -sd', ' -)"
            hint "These are runtime defaults — add them if a command complains."
            WARNED=1
        fi

        # Keys here that the template does not know about. A small allow-list of
        # keys injected at runtime (not part of the template) is expected.
        unknown="$(comm -13 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$env_keys"))"
        local KNOWN_EXTRA=" SPLENT_HOST_PROJECT_DIR SPLENT_APP SPLENT_ENV "
        local reported=""
        while IFS= read -r k; do
            [ -z "$k" ] && continue
            case "$KNOWN_EXTRA" in
                *" $k "*) ;;                       # expected runtime key
                *) reported="${reported}${k} " ;;
            esac
        done <<< "$unknown"
        if [ -n "$reported" ]; then
            warn "Overlapping/unknown keys not in .env.example: ${reported%% }"
            hint "Confirm they are intentional — typos here are a common silent bug."
            WARNED=1
        fi
        [ -z "$missing" ] && [ -z "$reported" ] && ok "No drift versus .env.example"
    fi

    summary
}

summary() {
    # Embedded in preflight: no summary banner, just propagate the exit code.
    if [ "$EMBED" = 1 ]; then
        [ "$FAILED" -ne 0 ] && exit 1
        exit 0
    fi
    echo
    if [ "$FAILED" -ne 0 ]; then
        printf "${C_RED}${C_BOLD}❌ .env has problems — resolve the items above.${C_RESET}\n\n"
        exit 1
    fi
    if [ "$WARNED" -ne 0 ]; then
        printf "${C_YELLOW}${C_BOLD}⚠ .env usable, with warnings.${C_RESET}\n\n"
    else
        printf "${C_GREEN}${C_BOLD}✅ .env looks good.${C_RESET}\n\n"
    fi
    exit 0
}

# ----------------------------------------------------------------------------
# fix-workdir
# ----------------------------------------------------------------------------
do_fix_workdir() {
    [ "$QUIET" = 1 ] || printf "\n${C_BOLD}🛠️  SPLENT fix-workdir${C_RESET}\n\n"
    ensure_env_file

    local cur_host dup_host; cur_host="$(env_get SPLENT_HOST_PROJECT_DIR)"
    dup_host="$(env_count SPLENT_HOST_PROJECT_DIR)"
    if [ "$cur_host" = "$EXPECTED_HOST_DIR" ] && [ "$dup_host" -le 1 ]; then
        ok "SPLENT_HOST_PROJECT_DIR already correct (${EXPECTED_HOST_DIR})"
    else
        env_upsert SPLENT_HOST_PROJECT_DIR "$EXPECTED_HOST_DIR"
        if [ "$dup_host" -gt 1 ]; then
            act "SPLENT_HOST_PROJECT_DIR collapsed ${dup_host} duplicate definitions → ${EXPECTED_HOST_DIR}"
        elif [ -z "$cur_host" ]; then
            act "SPLENT_HOST_PROJECT_DIR set to ${EXPECTED_HOST_DIR}"
        else
            act "SPLENT_HOST_PROJECT_DIR re-pointed: ${cur_host} → ${EXPECTED_HOST_DIR}"
        fi
    fi

    local cur_wd dup_wd; cur_wd="$(env_get WORKING_DIR)"
    dup_wd="$(env_count WORKING_DIR)"
    if [ "$cur_wd" = "/workspace" ] && [ "$dup_wd" -le 1 ]; then
        ok "WORKING_DIR already correct (/workspace)"
    else
        env_upsert WORKING_DIR "/workspace"
        if [ "$dup_wd" -gt 1 ]; then
            act "WORKING_DIR collapsed ${dup_wd} duplicate definitions → /workspace"
        else
            act "WORKING_DIR set to /workspace${cur_wd:+ (was ${cur_wd})}"
        fi
    fi
    [ "$QUIET" = 1 ] || echo
}

# ----------------------------------------------------------------------------
# fix-app
# ----------------------------------------------------------------------------
do_fix_app() {
    [ "$QUIET" = 1 ] || printf "\n${C_BOLD}🛠️  SPLENT fix-app${C_RESET}\n\n"
    ensure_env_file

    local app; app="$(env_get SPLENT_APP)"
    if [ -z "$app" ]; then
        ok "No product selected (already detached) — nothing to fix"
    elif [ -d "$WORKSPACE_DIR/$app" ]; then
        ok "SPLENT_APP = ${app} is valid — nothing to fix"
    else
        env_delete SPLENT_APP
        act "Cleared dangling SPLENT_APP='${app}' → detached mode"
        hint "Re-select a product later with: splent product:select <name>"
    fi
    [ "$QUIET" = 1 ] || echo
}

# ----------------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------------
case "$CMD" in
    check)        do_check ;;
    fix-workdir)  do_fix_workdir ;;
    fix-app)      do_fix_app ;;
    fix-env)      do_fix_workdir; do_fix_app; do_check ;;
    *)
        fail "Unknown command: '${CMD}'"
        hint "Usage: env_tools.sh <check|fix-workdir|fix-app|fix-env>"
        exit 2 ;;
esac

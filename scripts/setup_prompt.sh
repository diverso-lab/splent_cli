#!/bin/bash
set -e

HOME_DIR="/home/splent"
BASHRC="$HOME_DIR/.bashrc"

mkdir -p "$HOME_DIR"
touch "$BASHRC"

# ------------------------------------------------------------
# Clean previous SPLENT injections
# ------------------------------------------------------------
sed -i '/# --- SPLENT CLI enhancements ---/,/# --- END SPLENT CLI ---/d' "$BASHRC"

# ------------------------------------------------------------
# Inject clean SPLENT block
# ------------------------------------------------------------
cat <<'EOF' >> "$BASHRC"

# --- SPLENT CLI enhancements ---

load_splent_env() {
  if [ -z "${SPLENT_ENV_LOADED:-}" ] && [ -f /workspace/.env ]; then
    set -a
    . /workspace/.env
    set +a
    export SPLENT_ENV_LOADED=1
  fi
}

enter_workspace() {
  if [ -d /workspace ] && [ "$PWD" = "$HOME" ]; then
    cd /workspace
  fi
}

set_prompt() {
  local app_path="/workspace/${SPLENT_APP}"

  if [ -n "$SPLENT_APP" ] && [ -d "$app_path" ]; then
    PS1="\[\e[1;32m\](${SPLENT_APP})\[\e[0m\] \w\$ "
  elif [ -n "$SPLENT_APP" ] && [ ! -d "$app_path" ]; then
    PS1="\[\e[1;33m\](${SPLENT_APP}?)\[\e[0m\] \w\$ "
  else
    PS1="\[\e[90m\](detached)\[\e[0m\] \w\$ "
  fi
}

splent() {
  if [ "$1" = "product:select" ]; then
    shift
    eval "$(SPLENT_SILENT=1 command splent product:select "$@" --shell)"
  elif [ "$1" = "product:deselect" ]; then
    shift
    eval "$(command splent product:deselect --shell "$@")"
  else
    command splent "$@"
  fi
}

load_splent_env
enter_workspace
PROMPT_COMMAND=set_prompt

# Bash autocompletion for splent CLI
# We override the generated completion to use `command splent` (the binary)
# instead of the shell function wrapper.
_splent_completion() {
    local IFS=$'\n'
    local response
    response=$(COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _SPLENT_COMPLETE=bash_complete /usr/local/bin/splent)
    for completion in $response; do
        IFS=',' read type value <<< "$completion"
        if [[ $type == 'plain' ]]; then
            COMPREPLY+=($value)
        fi
    done
    return 0
}
complete -o nosort -F _splent_completion splent

# --- END SPLENT CLI ---
EOF
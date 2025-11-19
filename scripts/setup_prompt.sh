#!/bin/bash
set -e

HOME_DIR="/home/splent"
BASHRC="$HOME_DIR/.bashrc"

mkdir -p "$HOME_DIR"
touch "$BASHRC"

cat <<'EOF' >> "$BASHRC"

# --- SPLENT CLI enhancements ---

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

PROMPT_COMMAND=set_prompt

splent() {
  if [ "$1" = "product:select" ]; then
    shift
    eval "$(SPLENT_SILENT=1 command splent product:select "$@" --shell)"
  else
    command splent "$@"
  fi
}
EOF

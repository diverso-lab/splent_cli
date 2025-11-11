#!/bin/bash

# --- FIX definitivo de Git (system-level, no global) ---
# Esto se ejecuta cada vez que arranca el contenedor
git config --system --add safe.directory /workspace 2>/dev/null || true
for dir in /workspace/*; do
  [ -d "$dir" ] && git config --system --add safe.directory "$dir" 2>/dev/null || true
done
echo "✅ Git safe.directory configurado (system-level)."
# -------------------------------------------------------

# Si no se pasa ningún comando, mantiene vivo el contenedor
if [ $# -eq 0 ]; then
    echo "🚀 No command provided. Keeping container alive..."
    exec tail -f /dev/null
else
    # Ejecuta la CLI con los argumentos proporcionados
    exec splent "$@"
fi

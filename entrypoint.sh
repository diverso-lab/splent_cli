#!/bin/bash

# Si no se pasa ningún comando, ejecuta algo que lo mantenga vivo
if [ $# -eq 0 ]; then
    echo "No command provided. Keeping container alive..."
    tail -f /dev/null
else
    # Ejecuta la CLI con los argumentos proporcionados
    exec splent "$@"
fi

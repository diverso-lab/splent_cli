#!/bin/bash

# ---------------------------------------------------------------------------
# Creative Commons CC BY 4.0 - SPLENT - Diverso Lab
# ---------------------------------------------------------------------------
# This script is licensed under the Creative Commons Attribution 4.0 
# International License. You are free to share and adapt the material 
# as long as appropriate credit is given, a link to the license is provided, 
# and you indicate if changes were made.
#
# For more details, visit:
# https://creativecommons.org/licenses/by/4.0/
# ---------------------------------------------------------------------------

set -e

echo ""

TABLE_COUNT=$(mariadb -u "$MARIADB_USER" -p"$MARIADB_PASSWORD" -h "$MARIADB_HOSTNAME" -P 3306 -D "$MARIADB_DATABASE" -sse \
    "SELECT COUNT(*) FROM information_schema.tables
     WHERE table_schema = '$MARIADB_DATABASE'
     AND table_name NOT IN ('splent_migrations');")

if [ "$TABLE_COUNT" -eq 0 ]; then
    echo "    empty database, applying migrations..."
    splent db:upgrade
    if [ "$RUN_DB_SEED" = "true" ]; then
        echo "    seeding database..."
        splent db:seed -y
    fi
else
    echo "    applying pending migrations..."
    splent db:upgrade
fi

echo ""

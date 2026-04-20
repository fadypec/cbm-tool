#!/usr/bin/env bash
# db/migrate.sh — apply pending SQL migrations to the CBM database
#
# Migrations are plain .sql files in db/migrations/, applied in filename order.
# Applied filenames are recorded in the schema_migrations table so re-running
# this script is safe.
#
# Usage:
#   ./db/migrate.sh                           # uses DATABASE_URL from .env
#   DATABASE_URL=postgresql://... ./db/migrate.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

# Load DATABASE_URL from .env if not already in the environment
if [[ -z "${DATABASE_URL:-}" && -f "$ENV_FILE" ]]; then
    DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
fi
DATABASE_URL="${DATABASE_URL:-postgresql://cbm:cbm@localhost:5432/cbm}"

echo "Target: $DATABASE_URL"

# Ensure the migrations-tracking table exists
psql "$DATABASE_URL" --quiet -c "
    CREATE TABLE IF NOT EXISTS schema_migrations (
        filename   TEXT        PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"

# Apply each migration file in sorted order if not already recorded
MIGRATIONS_DIR="$SCRIPT_DIR/migrations"
applied=0
skipped=0

for migration in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
    fname="$(basename "$migration")"
    # Escape single quotes to prevent SQL injection via crafted filenames
    fname_escaped="${fname//\'/\'\'}"
    already_applied=$(psql "$DATABASE_URL" -t --quiet \
        -c "SELECT COUNT(*) FROM schema_migrations WHERE filename = '$fname_escaped'" \
        | tr -d ' \n')

    if [[ "$already_applied" == "0" ]]; then
        echo "  Applying $fname …"
        psql "$DATABASE_URL" --quiet -f "$migration"
        psql "$DATABASE_URL" --quiet \
            -c "INSERT INTO schema_migrations (filename) VALUES ('$fname_escaped')"
        echo "  ✓ $fname"
        applied=$((applied + 1))
    else
        echo "  · $fname (already applied)"
        skipped=$((skipped + 1))
    fi
done

echo ""
echo "Done: $applied applied, $skipped already up to date."

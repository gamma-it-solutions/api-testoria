#!/usr/bin/env bash
# Usage: wait-for-db.sh [host] [port] [user] [db]
# Polls pg_isready until Postgres accepts connections, then exits 0.
# Exits 1 if Postgres is not ready after $MAX_ATTEMPTS tries.

HOST="${1:-localhost}"
PORT="${2:-5432}"
USER="${3:-testoria}"
DB="${4:-testoria}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-30}"
SLEEP_SECONDS="${SLEEP_SECONDS:-2}"

echo "Waiting for Postgres at ${HOST}:${PORT} (db=${DB}, user=${USER})..."

attempt=1
until pg_isready -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -q; do
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        echo "Postgres not ready after ${MAX_ATTEMPTS} attempts — giving up." >&2
        exit 1
    fi
    echo "  attempt ${attempt}/${MAX_ATTEMPTS} — not ready, retrying in ${SLEEP_SECONDS}s..."
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
done

echo "Postgres is ready."

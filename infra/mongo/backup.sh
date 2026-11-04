#!/usr/bin/env sh
# Gzip archive of the application database through the Compose `mongo` service.
# Atlas Search/Vector indexes are not part of the dump: recreate them with the search index command after a restore.
set -eu
DB="${MONGO_DATABASE:-getnet_support}"
OUT_DIR="${BACKUP_DIR:-backups}"
mkdir -p "$OUT_DIR"
FILE="$OUT_DIR/$DB-$(date -u +%Y%m%dT%H%M%SZ).archive.gz"
docker compose exec -T mongo mongodump --quiet --db "$DB" --archive --gzip > "$FILE"
echo "$FILE"

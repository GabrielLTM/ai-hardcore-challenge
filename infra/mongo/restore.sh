#!/usr/bin/env sh
# Restore an archive into TARGET_DB, by default a separate verification database.
# --drop replaces the TARGET collections: set TARGET_DB to the live database only for a real recovery.
set -eu
FILE="${1:?usage: restore.sh <archive.gz>}"
DB="${MONGO_DATABASE:-getnet_support}"
TARGET="${TARGET_DB:-${DB}_restore_check}"
docker compose exec -T mongo mongorestore --quiet --archive --gzip --drop --nsFrom="$DB.*" --nsTo="$TARGET.*" < "$FILE"
docker compose exec -T mongo mongosh --quiet --eval "
  const t = db.getSiblingDB('$TARGET');
  t.getCollectionNames().sort().forEach(c => print(c + ': ' + t[c].countDocuments()));"

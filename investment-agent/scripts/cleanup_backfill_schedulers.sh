#!/bin/bash
# TDNET backfill scheduler 一括削除
set -euo pipefail

PROJECT="gmailpj-357912"
LOCATION="us-west1"

for i in $(seq 5 23); do
  name="tdnet-backfill-batch${i}"
  printf "Deleting %s ... " "$name"
  if gcloud scheduler jobs delete "$name" \
    --project="$PROJECT" --location="$LOCATION" --quiet 2>/dev/null; then
    echo "OK"
  else
    echo "SKIP (not found)"
  fi
done

echo "Done."

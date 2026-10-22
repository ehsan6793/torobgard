#!/bin/sh
set -e
if [ ! -f data/torob.db ]; then
  echo "unpacking dataset…"
  gunzip -k data/torob.db.gz
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --proxy-headers

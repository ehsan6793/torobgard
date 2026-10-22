#!/usr/bin/env bash
# start (or restart) the dev server on :8000
cd "$(dirname "$0")/.."
if [ -f /tmp/torob.pid ]; then kill "$(cat /tmp/torob.pid)" 2>/dev/null; sleep 1; fi
nohup python3 -m uvicorn app.main:app --port 8000 --host 127.0.0.1 > /tmp/uv.log 2>&1 &
echo $! > /tmp/torob.pid

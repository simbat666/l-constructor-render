#!/usr/bin/env bash
set -euo pipefail

python3 scripts/local_cad_api.py &
cad_pid=$!

cleanup() {
  kill "$cad_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm run start -- --hostname 0.0.0.0 --port "${PORT:-10000}" &
web_pid=$!

while kill -0 "$cad_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "$web_pid" 2>/dev/null; then
  wait "$web_pid"
else
  wait "$cad_pid"
fi

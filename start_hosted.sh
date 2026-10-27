#!/bin/bash
# Starts the demo API (internal, port 8000) and the web app (public, $PORT). If either stops the container exits so the host restarts it.
set -u
python -m harness.demo --port 8000 --keep &
(cd frontend && exec node node_modules/next/dist/bin/next start -H 0.0.0.0 -p "${PORT:-3000}") &
wait -n
echo "a process stopped; exiting so the host restarts the service" >&2
exit 1

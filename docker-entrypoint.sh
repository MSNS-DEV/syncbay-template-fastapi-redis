#!/bin/sh
# Run database migrations, then replace this shell with uvicorn so it receives SIGTERM directly.
set -eu
alembic upgrade head
exec uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers 1 --loop uvloop --http httptools \
  --timeout-graceful-shutdown 10 \
  --no-access-log --no-server-header --no-proxy-headers

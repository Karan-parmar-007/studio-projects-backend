#!/bin/sh
set -e
echo "Running database migrations..."
alembic upgrade head
echo "Starting API..."
# One worker on purpose: queued-job bookkeeping (gateway._JOBS) is in-process.
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

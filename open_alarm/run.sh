#!/bin/sh
set -e
exec python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8099

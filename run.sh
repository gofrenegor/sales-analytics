#!/bin/sh
cd "$(dirname "$0")"
exec python3 -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8501}"

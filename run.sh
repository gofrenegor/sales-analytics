#!/bin/sh
cd "$(dirname "$0")"
# Default one process. Ready JSON and uploads are shared, but each worker keeps
# its own pandas frames. Raise --workers only when RAM allows.
exec python3 -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8501}" --workers "${WEB_WORKERS:-1}" \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

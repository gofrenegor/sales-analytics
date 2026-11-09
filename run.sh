#!/bin/sh
cd "$(dirname "$0")"
# Keep one process: uploads, preview frames, locks and the in-process caches
# are deliberately process-local until a shared Redis/store is deployed.
exec python3 -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8501}" --workers 1 \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

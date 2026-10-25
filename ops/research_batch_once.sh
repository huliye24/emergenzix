#!/usr/bin/env bash
# Run exactly one approved historical research job in the *existing* API
# Docker Compose service. Reuse the API's /data SQLite volume and runtime
# image; do not start an unmanaged daemon or expose any additional port.
set -euo pipefail

usage() {
  printf 'Usage: bash ops/research_batch_once.sh BATCH_SHA256\n' >&2
  printf 'Runs one persisted BTC/USDT historical job; never places orders.\n' >&2
}

if [[ $# -ne 1 || ! "$1" =~ ^[0-9a-f]{64}$ ]]; then
  usage
  exit 2
fi

# The operator must invoke this script deliberately. The worker itself also
# enforces --execute-one and claim fencing. docker compose run does not publish
# the service's ports and --no-deps does not start any other service.
exec docker compose run --rm --no-deps -T api \
  python -m emergenzix_api.research_batch_worker \
  --batch-id "$1" \
  --db-path /data/emergenzix.db \
  --execute-one

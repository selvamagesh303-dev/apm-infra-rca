#!/usr/bin/env bash
# Drives extra traffic through the gateway (services also self-generate load).
# Usage: ./scripts/load-test.sh [count]
set -euo pipefail
COUNT="${1:-100}"
BASE="${BASE_URL:-http://localhost:8090}"
for ((i = 1; i <= COUNT; i++)); do
  curl -s -o /dev/null -X POST "$BASE/checkout/user-$((RANDOM % 200))" || true
  sleep 0.1
done
echo "sent $COUNT checkouts -> open dashboard http://localhost:8000"

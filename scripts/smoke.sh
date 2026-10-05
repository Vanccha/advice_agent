#!/usr/bin/env bash
# Health + seed sanity checks against the running stack.
set -uo pipefail
fail=0
check() { # name url expected_substring
  local name="$1" url="$2" expect="${3:-}"
  local body
  body="$(curl -fsS --max-time 5 "$url" 2>/dev/null)" || { echo "  FAIL  $name ($url unreachable)"; fail=1; return; }
  if [[ -n "$expect" && "$body" != *"$expect"* ]]; then
    echo "  FAIL  $name (missing '$expect')"; fail=1; return
  fi
  echo "  ok    $name"
}
echo "health:"
check "core-api"          "http://localhost:8001/health" '"status":"ok"'
check "ticketing"         "http://localhost:8003/health" '"status":"ok"'
check "notification-hub"  "http://localhost:8004/health" '"status":"ok"'
check "payment-gateway"   "http://localhost:8002/health" '"status":"ok"'
check "assistant"         "http://localhost:8080/health" '"status":"ok"'
check "prometheus"        "http://localhost:9091/-/healthy"
check "alertmanager"      "http://localhost:9093/-/healthy"
echo "openapi:"
check "core-api openapi"  "http://localhost:8001/openapi.json" '"openapi"'
echo "seed:"
packages="$(curl -fsS --max-time 5 http://localhost:8001/v1/packages 2>/dev/null)"
pkg_count="$(printf '%s' "$packages" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("total", len(d.get("items", []))))' 2>/dev/null || echo 0)"
[[ "$pkg_count" -ge 7 ]] && echo "  ok    packages seeded ($pkg_count)" || { echo "  FAIL  packages seeded ($pkg_count)"; fail=1; }
echo "ui:"
check "ticket panel"      "http://localhost:8003/agent"
check "channel ui"        "http://localhost:8004/"
check "chat widget"       "http://localhost:8080/"
exit $fail

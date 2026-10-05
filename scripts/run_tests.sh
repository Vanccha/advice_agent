#!/usr/bin/env bash
# Runs every test suite in its own pytest process.
# Services deliberately share the package name `app`, so they must not be collected together.
set -uo pipefail
cd /workspace
fail=0
declare -a failed=()

run() { # label  env-assignments... -- pytest-paths
  local label="$1"; shift
  echo ""
  echo "=== $label ==="
  if ! "$@"; then fail=1; failed+=("$label"); fi
}

PYTEST_EXTRA=("$@")

run "architecture" python -m pytest tests/architecture "${PYTEST_EXTRA[@]}"

for svc in company/*/; do
  svc="${svc%/}"
  [ -d "$svc/tests" ] || continue
  run "$svc" env PYTHONPATH="/workspace/company:/workspace/$svc" \
      python -m pytest "$svc/tests" "${PYTEST_EXTRA[@]}"
done

for svc in integrations/mcp_*/; do
  svc="${svc%/}"
  [ -d "$svc/tests" ] || continue
  run "$svc" env PYTHONPATH="/workspace/integrations:/workspace/$svc" \
      python -m pytest "$svc/tests" "${PYTEST_EXTRA[@]}"
done

if [ -d integrations/common/tests ]; then
  run "integrations/common" env PYTHONPATH="/workspace/integrations" \
      python -m pytest integrations/common/tests "${PYTEST_EXTRA[@]}"
fi

if compgen -G "assistant/*/tests" > /dev/null; then
  run "assistant" env PYTHONPATH="/workspace/assistant" \
      python -m pytest assistant "${PYTEST_EXTRA[@]}"
fi

if [ -d tests/integration ]; then
  run "integration" env PYTHONPATH="/workspace/assistant:/workspace/integrations" \
      python -m pytest tests/integration "${PYTEST_EXTRA[@]}"
fi

echo ""
if [ "$fail" -eq 0 ]; then
  echo "ALL SUITES PASSED"
else
  echo "FAILED SUITES: ${failed[*]}"
fi
exit $fail

#!/usr/bin/env bash
# End-to-end smoke test (Spec/09 §5 happy path; BUILD H.1).
#
# Two modes:
#   local   (default) — exercises the deterministic engine core: runs the kernel
#                       pipeline for a working union and asserts it reproduces the
#                       groundtruth above threshold. No AWS required.
#   deployed          — uploads a Rate Notice via the API presigned URL and polls
#                       the outputs bucket for the rendered sheet (needs a deployed
#                       stack + AWS creds + $API_BASE_URL).
#
# Usage:
#   tests/e2e/smoke-test.sh                       # local, union 704
#   UNION=pipe_fitters_537 tests/e2e/smoke-test.sh
#   MODE=deployed API_BASE_URL=https://... tests/e2e/smoke-test.sh
set -euo pipefail

MODE="${MODE:-local}"
UNION="${UNION:-sprinkler_fitters_704}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Accuracy floors per Spec/09 §4.1 for the working unions.
declare -A FLOOR=(
  [sprinkler_fitters_704]=99.0
  [sprinkler_fitters_483]=99.0
  [pipe_fitters_537]=67.0
)

log() { printf '\n=== %s ===\n' "$1"; }

run_local() {
  log "LOCAL smoke — kernel pipeline for ${UNION}"
  cd "${REPO_ROOT}/kernel"
  local out
  out="$(uv run python pipeline/run.py --union "${UNION}")"
  echo "${out}" | tail -5

  # Parse "OVERALL CELL ACCURACY: N/M = XX.X%"
  local acc
  acc="$(echo "${out}" | grep -oE 'OVERALL CELL ACCURACY: [0-9]+/[0-9]+ = [0-9.]+%' | grep -oE '[0-9.]+%$' | tr -d '%')"
  local floor="${FLOOR[${UNION}]:-0}"
  if [[ -z "${acc}" ]]; then
    echo "FAIL: could not parse accuracy"; exit 1
  fi
  echo "accuracy=${acc}%  floor=${floor}%"
  if awk "BEGIN{exit !(${acc} >= ${floor})}"; then
    echo "SMOKE PASS"
  else
    echo "SMOKE FAIL: ${acc}% < ${floor}%"; exit 1
  fi
}

run_deployed() {
  log "DEPLOYED smoke — upload + poll (${UNION})"
  : "${API_BASE_URL:?set API_BASE_URL to the deployed HTTP API endpoint}"
  : "${JWT:?set JWT to a Cognito id token (Admins/Operations)}"
  local fixture
  fixture="$(ls "${REPO_ROOT}/kernel/data/${UNION}/cba/"*"Rate Notice.pdf" 2>/dev/null | head -1 \
    || ls "${REPO_ROOT}/kernel/data/${UNION}/cba/"*.pdf | head -1)"
  echo "fixture: ${fixture}"

  # 1) request a presigned PUT URL
  local presign
  presign="$(curl -fsS -X POST "${API_BASE_URL}/v1/uploads" \
    -H "Authorization: Bearer ${JWT}" -H 'Content-Type: application/json' \
    -d "{\"filename\":\"$(basename "${fixture}")\"}")"
  local url key
  url="$(echo "${presign}" | python -c 'import json,sys;print(json.load(sys.stdin)["url"])')"
  key="$(echo "${presign}" | python -c 'import json,sys;print(json.load(sys.stdin)["key"])')"

  # 2) upload the PDF (triggers the pipeline via S3 -> EventBridge)
  curl -fsS -X PUT "${url}" --data-binary "@${fixture}" >/dev/null
  echo "uploaded ${key}; pipeline started. Poll the outputs bucket / Jobs UI."
  echo "DEPLOYED SMOKE: upload accepted (full assertion is a manual/UAT step)."
}

case "${MODE}" in
  local) run_local ;;
  deployed) run_deployed ;;
  *) echo "unknown MODE: ${MODE}"; exit 2 ;;
esac

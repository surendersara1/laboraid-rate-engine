#!/usr/bin/env bash
# LaborAid Rate Engine — end-to-end deploy.
#
# Brings up the whole system in the right order so that uploading a PDF runs
# extraction and lands outputs in S3 + rows in DynamoDB/Aurora:
#
#   preflight -> build UI bundle -> create ECR repo -> build & push the
#   ExtractorAgent image (ARM64) -> cdk bootstrap -> cdk deploy --all ->
#   seed agent-config (enable the agent) -> print outputs.
#
# WHY this order: ProcessingStack imports the ECR repo by name and its AgentCore
# runtime references {repo}:latest at DEPLOY time, so the repo + image must exist
# BEFORE `cdk deploy`. This script guarantees that.
#
# Usage:
#   scripts/deploy.sh [--env dev|prod] [--yes]
#                     [--skip-ui] [--skip-image] [--skip-bootstrap] [--smoke]
#
# Prerequisites (in the target AWS account, us-east-1):
#   - AWS credentials with admin-ish deploy rights (env vars or a profile).
#   - Amazon Bedrock model access ENABLED for Claude Sonnet + Haiku.
#   - Bedrock AgentCore Runtime available in the account/region.
#   - Tools on PATH: aws, docker (with buildx), node/npx, uv, corepack (pnpm).
#
# This script is idempotent: re-running re-pushes the image and re-deploys.
set -euo pipefail

# --- locate repo root (this file lives in scripts/) --------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

REGION="us-east-1"   # hard requirement: Bedrock + AgentCore availability
ENVIRONMENT="dev"
ASSUME_YES=0
SKIP_UI=0; SKIP_IMAGE=0; SKIP_BOOTSTRAP=0; RUN_SMOKE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENVIRONMENT="$2"; shift 2;;
    --yes|-y) ASSUME_YES=1; shift;;
    --skip-ui) SKIP_UI=1; shift;;
    --skip-image) SKIP_IMAGE=1; shift;;
    --skip-bootstrap) SKIP_BOOTSTRAP=1; shift;;
    --smoke) RUN_SMOKE=1; shift;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ "${ENVIRONMENT}" == "dev" || "${ENVIRONMENT}" == "prod" ]] || { echo "--env must be dev|prod" >&2; exit 2; }

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m    ✗ %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

# --- 0. preflight ------------------------------------------------------------
log "Preflight"
for t in aws docker node npx uv corepack; do need "$t"; done
docker buildx version >/dev/null 2>&1 || die "docker buildx is required (ARM64 build)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)" \
  || die "no AWS credentials (aws sts get-caller-identity failed)"
ok "AWS account ${ACCOUNT}, region ${REGION}, env ${ENVIRONMENT}"

REPO_NAME="laboraid-${ENVIRONMENT}-l5-ecr-agent-extractor"
REPO_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"
AGENT_CFG_TABLE="laboraid-${ENVIRONMENT}-l3-ddb-agent-config"

if [[ "${ASSUME_YES}" -ne 1 ]]; then
  read -r -p "    Deploy LaborAid '${ENVIRONMENT}' to account ${ACCOUNT} (${REGION})? [y/N] " a
  [[ "${a}" == "y" || "${a}" == "Y" ]] || die "aborted"
fi

# --- 1. UI bundle (must exist before the Ui stack deploys) -------------------
if [[ "${SKIP_UI}" -ne 1 ]]; then
  log "Build UI bundle (ui/dist)"
  ( cd ui && corepack pnpm install --frozen-lockfile && corepack pnpm build )
  [[ -s ui/dist/index.html ]] || die "ui/dist/index.html missing/empty after build"
  ok "UI built"
else
  [[ -s ui/dist/index.html ]] || die "--skip-ui but ui/dist/index.html is missing; build it first"
  ok "UI build skipped (existing ui/dist used)"
fi

# --- 2. ECR repo (create before the image push / stack deploy) --------------
log "Ensure ECR repo ${REPO_NAME}"
if ! aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  aws ecr create-repository \
    --repository-name "${REPO_NAME}" \
    --image-scanning-configuration scanOnPush=true \
    --region "${REGION}" >/dev/null
  ok "created ${REPO_NAME}"
else
  ok "repo already exists"
fi

# --- 3. build & push the ExtractorAgent image (ARM64) -----------------------
if [[ "${SKIP_IMAGE}" -ne 1 ]]; then
  log "Build & push ExtractorAgent image (linux/arm64)"
  aws ecr get-login-password --region "${REGION}" \
    | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
  # build context = repo root (Dockerfile copies kernel/ + agents/extractor/)
  docker buildx build --platform linux/arm64 \
    -f agents/extractor/Dockerfile \
    -t "${REPO_URI}:latest" \
    --push .
  ok "pushed ${REPO_URI}:latest"
else
  aws ecr describe-images --repository-name "${REPO_NAME}" --image-ids imageTag=latest \
    --region "${REGION}" >/dev/null 2>&1 \
    || die "--skip-image but ${REPO_URI}:latest not found in ECR"
  ok "image push skipped (existing :latest used)"
fi

# --- 4. cdk bootstrap (idempotent) ------------------------------------------
export CDK_DEFAULT_ACCOUNT="${ACCOUNT}" CDK_DEFAULT_REGION="${REGION}"
if [[ "${SKIP_BOOTSTRAP}" -ne 1 ]]; then
  log "cdk bootstrap aws://${ACCOUNT}/${REGION}"
  ( cd cdk && uv sync >/dev/null && npx --yes aws-cdk@2 bootstrap "aws://${ACCOUNT}/${REGION}" )
  ok "bootstrapped"
fi

# --- 5. deploy all stacks (CDK resolves the 9-stack dependency order) -------
log "cdk deploy --all (env=${ENVIRONMENT})"
( cd cdk && npx --yes aws-cdk@2 deploy --all \
    -c env="${ENVIRONMENT}" \
    --require-approval never \
    --outputs-file "cdk-outputs.${ENVIRONMENT}.json" )
ok "stacks deployed"

# --- 6. enable the ExtractorAgent (Step Functions reads this row) -----------
log "Seed agent-config (enable ExtractorAgent)"
aws dynamodb put-item \
  --table-name "${AGENT_CFG_TABLE}" \
  --region "${REGION}" \
  --item '{"agent_name":{"S":"ExtractorAgent"},"enabled":{"BOOL":true},"image_tag":{"S":"latest"},"updated_by":{"S":"deploy.sh"}}' \
  >/dev/null && ok "ExtractorAgent enabled"

# --- 7. summary + verification pointers -------------------------------------
log "Done — outputs in cdk/cdk-outputs.${ENVIRONMENT}.json"
echo "    Key resources (env=${ENVIRONMENT}):"
echo "      inputs  bucket : laboraid-${ENVIRONMENT}-l3-bucket-inputs"
echo "      outputs bucket : laboraid-${ENVIRONMENT}-l3-bucket-outputs"
echo "      Aurora DB      : laboraid  (tables: unions, rate_periods, rate_cells, audit_log)"
echo "      DynamoDB       : laboraid-${ENVIRONMENT}-l3-ddb-{files,jobs,review,overrides,cadence,idempotency,agent-config}"
echo
echo "    Verify end-to-end — upload a Rate Notice to the inputs bucket:"
echo "      aws s3 cp 'kernel/data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf' \\"
echo "        s3://laboraid-${ENVIRONMENT}-l3-bucket-inputs/laboraid/Sprinkler/704/2026-01-01/ --region ${REGION}"
echo "    then watch the Step Functions execution; the rendered xlsx/csv land in the outputs bucket"
echo "    and a rate_periods row + rate_cells appear in Aurora. See docs/DEPLOY.md."

if [[ "${RUN_SMOKE}" -eq 1 ]]; then
  log "Local kernel smoke (deterministic, no AWS)"
  ( cd kernel && uv run python pipeline/run.py --all --min-accuracy 99.0 )
fi

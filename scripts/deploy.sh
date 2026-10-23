#!/usr/bin/env bash
# Deploy one environment end to end: images -> Terraform -> website.
#
#   bash scripts/deploy.sh dev        (or prod)
#
# Needs: aws CLI (logged in), terraform >= 1.10, docker with buildx, python3, curl, git.
# The same script runs locally and in GitHub Actions (.github/workflows/deploy.yml).
set -euo pipefail

ENVIRONMENT="${1:-}"
if [[ "$ENVIRONMENT" != "dev" && "$ENVIRONMENT" != "prod" ]]; then
  echo "Usage: $0 <dev|prod>" >&2
  exit 1
fi

PROJECT_NAME="${PROJECT_NAME:-juniper-hollow}" # must match project_name in terraform.tfvars
AWS_REGION="${DEFAULT_AWS_REGION:-us-east-1}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME_PREFIX="${PROJECT_NAME}-${ENVIRONMENT}"

for tool in aws terraform docker python3 curl git; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
STATE_BUCKET="${PROJECT_NAME}-terraform-state-${AWS_ACCOUNT_ID}" # created by terraform/bootstrap

# Image tag: the commit SHA. ECR tags are immutable, so uncommitted local changes get a unique tag
# instead of silently reusing the image already pushed for this commit.
IMAGE_TAG="${GITHUB_SHA:-$(git -C "$ROOT_DIR" rev-parse HEAD)}"
if [[ -z "${GITHUB_SHA:-}" && -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
  IMAGE_TAG="${IMAGE_TAG}-local-$(date +%Y%m%d%H%M%S)"
fi

echo "==> Deploying ${NAME_PREFIX} to account ${AWS_ACCOUNT_ID} (${AWS_REGION}), image tag ${IMAGE_TAG}"

TF_VARS=(-var "image_tag=${IMAGE_TAG}")
if [[ "$ENVIRONMENT" == "prod" ]]; then
  TF_VARS+=(-var-file=prod.tfvars)
fi

cd "$ROOT_DIR/terraform"

echo "==> Terraform init (state bucket ${STATE_BUCKET})"
terraform init -input=false \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="region=${AWS_REGION}"
terraform workspace select -or-create "$ENVIRONMENT"

# 1. The image repositories must exist before the images can be pushed.
echo "==> Creating the ECR repositories"
terraform apply -input=false -auto-approve "${TF_VARS[@]}" \
  -target=aws_ecr_repository.app -target=aws_ecr_lifecycle_policy.app

# 2. Build and push both images (skipped if this tag is already in ECR).
echo "==> Logging in to ECR"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

build_and_push() { # <repository name> <platform> <dockerfile>
  local repository="$1" platform="$2" dockerfile="$3"
  if aws ecr describe-images --region "$AWS_REGION" --repository-name "$repository" \
       --image-ids "imageTag=${IMAGE_TAG}" >/dev/null 2>&1; then
    echo "==> ${repository}:${IMAGE_TAG} already in ECR; skipping build"
    return
  fi
  echo "==> Building and pushing ${repository}:${IMAGE_TAG} (${platform})"
  # --provenance=false: Lambda and AgentCore need a plain single-platform image manifest.
  docker buildx build --platform "$platform" --provenance=false \
    -f "$ROOT_DIR/$dockerfile" -t "${REGISTRY}/${repository}:${IMAGE_TAG}" --push "$ROOT_DIR"
}
build_and_push "${NAME_PREFIX}-lambda" linux/amd64 Dockerfile
build_and_push "${NAME_PREFIX}-agentcore" linux/arm64 Dockerfile.agentcore

# 3. Everything else: queue, secret, functions, AgentCore runtime, API, schedules, website app.
echo "==> Terraform apply"
terraform apply -input=false -auto-approve "${TF_VARS[@]}"

API_URL="$(terraform output -raw api_url)"
WEBSITE_URL="$(terraform output -raw website_url)"
AMPLIFY_APP_ID="$(terraform output -raw amplify_app_id)"
AMPLIFY_BRANCH="$(terraform output -raw amplify_branch_name)"
APP_SECRET_NAME="$(terraform output -raw app_secret_name)"

# 4. Website: copy frontend/, point config.js at this environment's API, zip, and publish to Amplify.
echo "==> Publishing the website to Amplify"
SITE_DIR="$(mktemp -d)"
trap 'rm -rf "$SITE_DIR"' EXIT
cp -R "$ROOT_DIR/frontend/." "$SITE_DIR/site"
printf '// Written by scripts/deploy.sh for %s.\nwindow.API_BASE_URL = "%s";\n' "$ENVIRONMENT" "$API_URL" \
  > "$SITE_DIR/site/config.js"
python3 -c 'import shutil, sys; shutil.make_archive(sys.argv[1], "zip", root_dir=sys.argv[2])' \
  "$SITE_DIR/site" "$SITE_DIR/site"

read -r JOB_ID UPLOAD_URL < <(aws amplify create-deployment --region "$AWS_REGION" \
  --app-id "$AMPLIFY_APP_ID" --branch-name "$AMPLIFY_BRANCH" \
  --query '[jobId, zipUploadUrl]' --output text)
curl --fail --silent --show-error -T "$SITE_DIR/site.zip" "$UPLOAD_URL"
aws amplify start-deployment --region "$AWS_REGION" \
  --app-id "$AMPLIFY_APP_ID" --branch-name "$AMPLIFY_BRANCH" --job-id "$JOB_ID" >/dev/null

STATUS=""
for _ in $(seq 1 60); do # up to 5 minutes
  STATUS="$(aws amplify get-job --region "$AWS_REGION" --app-id "$AMPLIFY_APP_ID" \
    --branch-name "$AMPLIFY_BRANCH" --job-id "$JOB_ID" --query 'job.summary.status' --output text)"
  [[ "$STATUS" == "SUCCEED" || "$STATUS" == "FAILED" || "$STATUS" == "CANCELLED" ]] && break
  sleep 5
done
if [[ "$STATUS" != "SUCCEED" ]]; then
  echo "Amplify deployment ${JOB_ID} ended with status ${STATUS}" >&2
  exit 1
fi

# 5. The app cannot start until its keys are in Secrets Manager (filled by hand, never by Terraform).
if ! aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$APP_SECRET_NAME" \
     --query VersionId --output text >/dev/null 2>&1; then
  echo
  echo "WARNING: secret ${APP_SECRET_NAME} has no value yet. Add the app's keys as JSON (see"
  echo "terraform/secrets.tf) in the Secrets Manager console; the API and agent fail until then."
fi

echo
echo "Deployed ${NAME_PREFIX}"
echo "  Website: ${WEBSITE_URL}"
echo "  API:     ${API_URL}"

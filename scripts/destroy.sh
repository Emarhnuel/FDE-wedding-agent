#!/usr/bin/env bash
# Destroy one environment's stack (never the bootstrap state bucket or the GitHub deploy role).
#
#   bash scripts/destroy.sh dev        (or prod)
#
# Asks you to type the environment name first; in GitHub Actions (CI=true) the destroy workflow
# has already asked for that confirmation.
set -euo pipefail

ENVIRONMENT="${1:-}"
if [[ "$ENVIRONMENT" != "dev" && "$ENVIRONMENT" != "prod" ]]; then
  echo "Usage: $0 <dev|prod>" >&2
  exit 1
fi

PROJECT_NAME="${PROJECT_NAME:-juniper-hollow}" # must match project_name in terraform.tfvars
AWS_REGION="${DEFAULT_AWS_REGION:-us-east-1}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

for tool in aws terraform; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="${PROJECT_NAME}-terraform-state-${AWS_ACCOUNT_ID}"

echo "This permanently deletes ${PROJECT_NAME}-${ENVIRONMENT} in account ${AWS_ACCOUNT_ID} (${AWS_REGION}):"
echo "the website, API, functions, agent runtime, queues, uploaded knowledge files, and images."
if [[ "${CI:-}" != "true" ]]; then
  read -r -p "Type the environment name (${ENVIRONMENT}) to confirm: " CONFIRMATION
  if [[ "$CONFIRMATION" != "$ENVIRONMENT" ]]; then
    echo "Cancelled." >&2
    exit 1
  fi
fi

# Destroy doesn't use the image, but the variable is required.
TF_VARS=(-var "image_tag=destroy" -var "force_destroy_buckets=true")
if [[ "$ENVIRONMENT" == "prod" ]]; then
  TF_VARS=(-var-file=prod.tfvars "${TF_VARS[@]}")
fi

cd "$ROOT_DIR/terraform"
terraform init -input=false \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="region=${AWS_REGION}"

if ! terraform workspace select "$ENVIRONMENT" 2>/dev/null; then
  echo "Workspace ${ENVIRONMENT} does not exist; nothing to destroy."
  exit 0
fi

# Turn on force_destroy first, so the bucket (with every file version) and the ECR repositories (with
# their images) can be deleted even when they are not empty. prod keeps it off in normal deploys.
echo "==> Allowing the bucket and repositories to be deleted with their contents"
terraform apply -input=false -auto-approve "${TF_VARS[@]}" \
  -target=aws_s3_bucket.knowledge -target=aws_ecr_repository.app

echo "==> Terraform destroy"
terraform destroy -input=false -auto-approve "${TF_VARS[@]}"

echo
echo "Destroyed ${PROJECT_NAME}-${ENVIRONMENT}."
if [[ "$ENVIRONMENT" == "prod" ]]; then
  echo "Note: the prod secret stays recoverable for 30 days, so its name can't be reused until then."
fi

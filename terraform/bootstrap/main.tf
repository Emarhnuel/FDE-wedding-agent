data "aws_caller_identity" "current" {}

locals {
  # Bucket names are global across AWS, so the account ID keeps this one unique.
  state_bucket_name = "${var.project_name}-terraform-state-${data.aws_caller_identity.current.account_id}"
  github_oidc_url   = "https://token.actions.githubusercontent.com"
  github_oidc_arn = (
    var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )
  # Immutable subject format (GitHub, July 2026): the old repo:OWNER/REPO:... format would not match.
  github_subjects = [
    for env in var.environments :
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:environment:${env}"
  ]
}

# -----------------------------------------------------------------------------
# Terraform state bucket (one state file per workspace: dev, prod)
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "terraform_state" {
  bucket = local.state_bucket_name

  # Losing this bucket loses track of every deployed resource.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning lets a damaged or deleted state file be recovered.
resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# State files contain secrets, so the bucket is never public.
resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# GitHub Actions login (OIDC): no long-lived AWS keys stored in GitHub
# -----------------------------------------------------------------------------

# AWS fetches GitHub's certificate thumbprint itself, so none is set here.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = local.github_oidc_url
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1

  url = local.github_oidc_url
}

data "aws_iam_policy_document" "github_deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only workflow jobs in this repository's dev and prod environments may deploy.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_trust.json
}

# Full access, as decided for this course; narrow it when hardening.
resource "aws_iam_role_policy_attachment" "github_deploy_admin" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

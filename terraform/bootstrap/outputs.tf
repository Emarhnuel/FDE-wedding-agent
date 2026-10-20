output "aws_region" {
  description = "Region of the state bucket; use the same region in the main stack."
  value       = var.aws_region
}

output "aws_account_id" {
  description = "GitHub secret AWS_ACCOUNT_ID."
  value       = data.aws_caller_identity.current.account_id
}

output "state_bucket_name" {
  description = "Bucket for the main stack's S3 backend (terraform/backend.tf)."
  value       = aws_s3_bucket.terraform_state.bucket
}

output "github_deploy_role_arn" {
  description = "GitHub secret AWS_ROLE_ARN, assumed by the deploy and destroy workflows."
  value       = aws_iam_role.github_deploy.arn
}

output "github_oidc_subjects" {
  description = "OIDC subjects the deploy role trusts, one per GitHub environment."
  value       = local.github_subjects
}

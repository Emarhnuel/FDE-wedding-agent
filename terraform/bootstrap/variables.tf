variable "aws_region" {
  description = "AWS region for the state bucket and the whole stack."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for resource names."
  type        = string
  default     = "juniper-hollow"
}

# GitHub's OIDC `sub` claim for repositories created after July 15, 2026 includes immutable owner and
# repository IDs: repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:ENV. Look them up with
# https://api.github.com/repos/OWNER/REPO (fields owner.id and id).
variable "github_owner" {
  description = "GitHub user or organization that owns the repository."
  type        = string
  default     = "Emarhnuel"
}

variable "github_owner_id" {
  description = "Immutable numeric ID of the GitHub owner."
  type        = number
  default     = 147157655
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "FDE-wedding-agent"
}

variable "github_repo_id" {
  description = "Immutable numeric ID of the GitHub repository."
  type        = number
  default     = 1416269501
}

variable "environments" {
  description = "GitHub environments (and Terraform workspaces) allowed to deploy."
  type        = list(string)
  default     = ["dev", "prod"]
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set to false if this AWS account already has one (only one per URL is allowed)."
  type        = bool
  default     = true
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = terraform.workspace
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  environment = terraform.workspace
  is_prod     = terraform.workspace == "prod"
  # Every resource name starts with this, e.g. juniper-hollow-dev-jobs.fifo.
  name_prefix = "${var.project_name}-${terraform.workspace}"
  account_id  = data.aws_caller_identity.current.account_id
}

# Fails the plan outside the dev and prod workspaces, so nothing is ever deployed from "default".
resource "terraform_data" "workspace_guard" {
  lifecycle {
    precondition {
      condition     = contains(["dev", "prod"], terraform.workspace)
      error_message = "Select the dev or prod workspace first: terraform workspace select dev (or prod)."
    }
  }
}

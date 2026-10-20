# One-time setup for the main stack: run by hand with local state (no backend block),
# because this creates the S3 bucket the main stack keeps its state in.
terraform {
  # 1.10+ for the S3 backend's native lock file (use_lockfile) used by the main stack.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform-bootstrap"
    }
  }
}

terraform {
  # 1.10+ for the S3 backend's native lock file (use_lockfile in backend.tf).
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

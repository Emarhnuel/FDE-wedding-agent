# Image repository, tagged with the git commit SHA (var.image_tag):
#   lambda - one image for the api, worker, and cron functions

locals {
  ecr_repositories = {
    lambda = "${local.name_prefix}-lambda"
  }
}

resource "aws_ecr_repository" "app" {
  for_each = local.ecr_repositories

  name = each.value
  # A commit's image can never be overwritten, so a deploy always runs exactly what was built.
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_destroy_buckets

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keep the newest images for rollbacks; older ones are deleted automatically.
resource "aws_ecr_lifecycle_policy" "app" {
  for_each = aws_ecr_repository.app

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 newest images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

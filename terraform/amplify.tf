# The website (frontend/) on Amplify Hosting, with no Git connection: the deploy script writes the
# API URL into config.js, zips frontend/, and publishes it with create-deployment + start-deployment.
# Amplify serves it over HTTPS at https://<branch>.<app id>.amplifyapp.com (no custom domain).

resource "aws_amplify_app" "website" {
  name     = "${local.name_prefix}-website"
  platform = "WEB" # static site, no server-side rendering
}

resource "aws_amplify_branch" "website" {
  app_id      = aws_amplify_app.website.id
  branch_name = local.environment
  stage       = local.is_prod ? "PRODUCTION" : "DEVELOPMENT"
  # Nothing builds automatically; only the deploy script publishes.
  enable_auto_build = false
}

locals {
  website_url = "https://${aws_amplify_branch.website.branch_name}.${aws_amplify_app.website.default_domain}"
}

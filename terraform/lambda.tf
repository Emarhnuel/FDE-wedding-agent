# Three functions from one image (built --provenance=false, single architecture, as Lambda requires):
#   api    - FastAPI through Mangum, behind API Gateway
#   worker - SQS jobs queue -> runs each agent or indexing job (src/runtime.py)
#   cron   - Gmail polling and the daily jobs (EventBridge Scheduler, in scheduler.tf)

locals {
  lambda_image_uri = "${aws_ecr_repository.app["lambda"].repository_url}:${var.image_tag}"

  # Handler for each function; it replaces the image's CMD.
  lambda_handlers = {
    api    = "src.api.handler"
    worker = "src.worker.handler"
    cron   = "src.cron.handler"
  }

  # Browser origins allowed by the API (CORS): the Amplify website plus any extras (e.g. localhost in dev).
  allowed_origins = concat([local.website_url], var.extra_allowed_origins)

  # Plain settings shared by the Lambda functions. Keys and connection strings are not here:
  # src/__init__.py loads them from the secret named by APP_SECRET_ARN.
  app_environment = {
    APP_SECRET_ARN    = aws_secretsmanager_secret.app.arn
    COMPANY_ID        = var.company_id
    VENUE_TIMEZONE    = var.venue_timezone
    AUTO_SEND_ROUTINE = tostring(var.auto_send_routine)
    JOBS_QUEUE_URL    = aws_sqs_queue.jobs.url
    KNOWLEDGE_BUCKET  = aws_s3_bucket.knowledge.bucket
    ALLOWED_ORIGINS   = join(",", local.allowed_origins)
    LANGSMITH_TRACING = "true"
    LANGSMITH_PROJECT = var.langsmith_project
    # Longest a job may run, so the daily cron can tell a cut-off run from a slow one.
    JOB_TIMEOUT_MINUTES = tostring(ceil(var.lambda_settings["worker"].timeout_seconds / 60))
  }
}

# Created before the functions so Terraform owns the log retention.
resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.lambda_handlers

  name              = "/aws/lambda/${local.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "app" {
  for_each = local.lambda_handlers

  function_name = "${local.name_prefix}-${each.key}"
  role          = aws_iam_role.lambda[each.key].arn
  package_type  = "Image"
  image_uri     = local.lambda_image_uri
  architectures = ["x86_64"]
  memory_size   = var.lambda_settings[each.key].memory_mb
  timeout       = var.lambda_settings[each.key].timeout_seconds

  image_config {
    command = [each.value]
  }

  environment {
    variables = local.app_environment
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.lambda]
}

# The worker receives jobs from the FIFO queue. Partial batch responses retry only the failed
# messages (src/worker.py returns batchItemFailures). It takes one job per invocation, so each job
# gets the full timeout.
resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn        = aws_sqs_queue.jobs.arn
  function_name           = aws_lambda_function.app["worker"].arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  # Caps the worker's share of the account's Lambda concurrency. AWS recommends this over reserved
  # concurrency for SQS, and reserved concurrency must leave 100 units unreserved anyway.
  scaling_config {
    maximum_concurrency = var.worker_max_concurrency
  }
}

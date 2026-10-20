# Three functions from one image (built --provenance=false, single architecture, as Lambda requires):
#   api    - FastAPI through Mangum, behind API Gateway
#   worker - SQS jobs queue -> AgentCore runtime
#   cron   - Gmail polling and the daily jobs (EventBridge Scheduler, in scheduler.tf)

locals {
  lambda_image_uri = "${aws_ecr_repository.app["lambda"].repository_url}:${var.image_tag}"

  # Handler for each function; it replaces the image's CMD.
  lambda_handlers = {
    api    = "src.api.handler"
    worker = "src.worker.handler"
    cron   = "src.cron.handler"
  }

  # Browser origins allowed by the API (CORS). amplify.tf (bundle 5) adds the website's URL.
  allowed_origins = var.extra_allowed_origins

  # Plain settings shared by the Lambda functions and the AgentCore runtime. Keys and connection
  # strings are not here: src/__init__.py loads them from the secret named by APP_SECRET_ARN.
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
    variables = merge(
      local.app_environment,
      each.key == "worker" ? { AGENT_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.app.agent_runtime_arn } : {},
    )
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.lambda]
}

# The worker receives jobs from the FIFO queue. Partial batch responses retry only the failed
# messages (src/worker.py returns batchItemFailures); FIFO allows at most 10 messages per batch.
resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn        = aws_sqs_queue.jobs.arn
  function_name           = aws_lambda_function.app["worker"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}

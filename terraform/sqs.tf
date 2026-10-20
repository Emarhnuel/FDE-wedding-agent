# FIFO jobs queue (JOBS_QUEUE_URL). The API sends one message per job with MessageGroupId = the
# conversation thread and an explicit MessageDeduplicationId, so jobs in one thread run in order.

locals {
  # AWS: at least six times the consuming Lambda's timeout, so Lambda can retry a throttled batch.
  jobs_visibility_timeout_seconds = 6 * var.lambda_settings["worker"].timeout_seconds
}

# Messages the worker keeps failing to dispatch end up here for staff to inspect.
# A FIFO queue's dead-letter queue must also be FIFO.
resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${local.name_prefix}-jobs-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "jobs" {
  name       = "${local.name_prefix}-jobs.fifo"
  fifo_queue = true
  # The API always sends its own deduplication ID.
  content_based_deduplication = false
  visibility_timeout_seconds  = local.jobs_visibility_timeout_seconds
  message_retention_seconds   = 345600 # 4 days; the jobs table keeps the durable record
  sqs_managed_sse_enabled     = true

  # A job whose thread is busy is retried later (worker RetryLater), so allow several receives.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 10
  })
}

# Only the jobs queue may send its failed messages to the dead-letter queue.
resource "aws_sqs_queue_redrive_allow_policy" "jobs_dlq" {
  queue_url = aws_sqs_queue.jobs_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}

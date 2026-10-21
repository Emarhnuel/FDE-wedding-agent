# EventBridge Scheduler runs the cron Lambda (src/cron.py) on two schedules, in the venue's time zone
# (daylight saving handled automatically):
#   poll-gmail - {"task": "poll_gmail"}: queue new customer emails from the shared inbox
#   daily      - any other input: retention selection, stuck-job recovery, 120-day cleanup
# Delivery is at least once, so a run can repeat; every job has a dedupe key, so repeats are harmless.

locals {
  cron_schedules = {
    poll-gmail = {
      description = "Queue new customer emails from the shared Gmail inbox"
      expression  = var.gmail_poll_schedule
      input       = jsonencode({ task = "poll_gmail" })
      # A missed poll is covered by the next one a few minutes later.
      max_event_age_seconds = 300
    }
    daily = {
      description           = "Retention outreach selection, stuck-job recovery, and the 120-day cleanup"
      expression            = var.daily_schedule
      input                 = jsonencode({ task = "daily" })
      max_event_age_seconds = 3600
    }
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    # Only schedules in this account may use the role (confused-deputy protection).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "scheduler_invoke_cron" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.app["cron"].arn, "${aws_lambda_function.app["cron"].arn}:*"]
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler_invoke_cron" {
  name   = "invoke-cron-lambda"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke_cron.json
}

resource "aws_scheduler_schedule" "cron" {
  for_each = local.cron_schedules

  name                         = "${local.name_prefix}-${each.key}"
  description                  = each.value.description
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.venue_timezone

  # Required by Scheduler; OFF runs exactly on schedule.
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.app["cron"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = each.value.input

    # Retries only a failed hand-off to Lambda; a run that fails inside the function is not retried.
    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = each.value.max_event_age_seconds
    }
  }

  depends_on = [aws_iam_role_policy.scheduler_invoke_cron]
}

# Scheduler invokes Lambda asynchronously, and Lambda would retry a failed run twice by default.
# No retries: the next poll (or the next day's run) picks up anything left over.
resource "aws_lambda_function_event_invoke_config" "cron" {
  function_name                = aws_lambda_function.app["cron"].function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 3600
}

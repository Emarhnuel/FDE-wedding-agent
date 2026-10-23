# Execution roles. As decided for this course they use AWS-managed full-access policies per service;
# narrow them to the exact resources when hardening.

# -----------------------------------------------------------------------------
# Lambda functions: api, worker, cron
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

locals {
  # Every function writes logs and reads the app secret (APP_SECRET_ARN, loaded in src/__init__.py).
  lambda_base_policies = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    "arn:aws:iam::aws:policy/SecretsManagerReadWrite",
  ]
  lambda_function_policies = {
    # Queues jobs and stores uploaded knowledge files.
    api = [
      "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
      "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    ]
    # Reads the jobs queue (event source mapping) and runs the jobs, which includes downloading
    # uploaded originals for indexing.
    worker = [
      "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
      "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    ]
    # Queues Gmail inquiries and retention jobs.
    cron = [
      "arn:aws:iam::aws:policy/AmazonSQSFullAccess",
    ]
  }
  lambda_policy_attachments = merge([
    for function, policies in local.lambda_function_policies : {
      for policy in concat(local.lambda_base_policies, policies) :
      "${function}:${element(split("/", policy), length(split("/", policy)) - 1)}" => {
        function = function
        policy   = policy
      }
    }
  ]...)
}

resource "aws_iam_role" "lambda" {
  for_each = local.lambda_function_policies

  name               = "${local.name_prefix}-${each.key}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "lambda" {
  for_each = local.lambda_policy_attachments

  role       = aws_iam_role.lambda[each.value.function].name
  policy_arn = each.value.policy
}

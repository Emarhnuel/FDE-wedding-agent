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
    # Reads the jobs queue (event source mapping) and invokes the AgentCore runtime.
    worker = [
      "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
      "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
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

# -----------------------------------------------------------------------------
# AgentCore runtime: runs agent jobs and document indexing (src/runtime.py)
# -----------------------------------------------------------------------------

# Trust policy from the AgentCore Runtime docs, limited to this account's AgentCore resources.
data "aws_iam_policy_document" "agentcore_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${var.aws_region}:${local.account_id}:*"]
    }
  }
}

# Execution permissions the AgentCore Runtime docs list for a container agent: pull the image,
# write logs, send traces and metrics.
data "aws_iam_policy_document" "agentcore_runtime" {
  statement {
    sid       = "ECRImageAccess"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.app["agentcore"].arn]
  }

  statement {
    sid       = "ECRTokenAccess"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:DescribeLogStreams", "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }
}

resource "aws_iam_role" "agentcore" {
  name               = "${local.name_prefix}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

resource "aws_iam_role_policy" "agentcore_runtime" {
  name   = "agentcore-runtime"
  role   = aws_iam_role.agentcore.id
  policy = data.aws_iam_policy_document.agentcore_runtime.json
}

# The runtime also reads the app secret and downloads uploaded originals for indexing.
resource "aws_iam_role_policy_attachment" "agentcore" {
  for_each = toset([
    "arn:aws:iam::aws:policy/SecretsManagerReadWrite",
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
  ])

  role       = aws_iam_role.agentcore.name
  policy_arn = each.value
}

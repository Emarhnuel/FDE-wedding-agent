# Values for dev are in terraform.tfvars (loaded automatically); prod adds -var-file=prod.tfvars.
# Secrets (API keys, DATABASE_URL, Gmail credentials) are never variables: they live in Secrets Manager.

# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region; must offer Bedrock AgentCore Runtime and match the bootstrap region."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for resource names, followed by the workspace (dev or prod)."
  type        = string
  default     = "juniper-hollow"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.project_name))
    error_message = "Use 2-31 lowercase letters, digits, or dashes, starting with a letter (it is part of S3 bucket names)."
  }
}

variable "image_tag" {
  description = "Tag of the Lambda and AgentCore images in ECR; the deploy script passes the git commit SHA."
  type        = string
  nullable    = false
}

variable "log_retention_days" {
  description = "Days to keep CloudWatch logs for the Lambda functions."
  type        = number
  default     = 14

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "Use a CloudWatch Logs retention value such as 7, 14, 30, or 90."
  }
}

variable "force_destroy_buckets" {
  description = "Let terraform destroy delete S3 buckets and ECR repositories that still hold objects or images."
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Application settings (plain environment variables, not secrets)
# -----------------------------------------------------------------------------

variable "company_id" {
  description = "Company the API serves (COMPANY_ID)."
  type        = string
  default     = "juniper-hollow"
}

variable "venue_timezone" {
  description = "IANA time zone for retention dates and the daily schedule (VENUE_TIMEZONE)."
  type        = string
  default     = "America/Chicago"
}

variable "auto_send_routine" {
  description = "Let routine, source-backed inquiry replies send without staff review (AUTO_SEND_ROUTINE)."
  type        = bool
  default     = false
}

variable "langsmith_project" {
  description = "LangSmith project for traces (LANGSMITH_PROJECT)."
  type        = string
  default     = "juniper-hollow-agent"
}

variable "extra_allowed_origins" {
  description = "Browser origins allowed to call the API besides the Amplify site (e.g. a local dev server)."
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Compute
# -----------------------------------------------------------------------------

variable "lambda_settings" {
  description = "Memory (MB) and timeout (seconds) for each Lambda function."
  type = map(object({
    memory_mb       = number
    timeout_seconds = number
  }))
  default = {
    api    = { memory_mb = 1024, timeout_seconds = 29 }
    worker = { memory_mb = 512, timeout_seconds = 60 }
    cron   = { memory_mb = 1024, timeout_seconds = 300 }
  }

  validation {
    condition     = toset(keys(var.lambda_settings)) == toset(["api", "worker", "cron"])
    error_message = "Set exactly the api, worker, and cron functions."
  }
  validation {
    condition = alltrue([
      for s in values(var.lambda_settings) :
      s.memory_mb >= 128 && s.memory_mb <= 10240 && s.timeout_seconds >= 1 && s.timeout_seconds <= 900
    ])
    error_message = "Lambda memory must be 128-10240 MB and timeout 1-900 seconds."
  }
  validation {
    condition     = var.lambda_settings["api"].timeout_seconds <= 30
    error_message = "The api function's timeout must be 30 seconds or less (the API Gateway integration limit)."
  }
}

# -----------------------------------------------------------------------------
# Public API rate limit (API Gateway default route throttling)
# -----------------------------------------------------------------------------

variable "api_throttling_rate_limit" {
  description = "Steady requests per second allowed on the API, protecting the public inquiry form."
  type        = number
  default     = 5

  validation {
    condition     = var.api_throttling_rate_limit > 0 && var.api_throttling_rate_limit <= 10000
    error_message = "Use 1-10000 requests per second (the account's default API Gateway quota)."
  }
}

variable "api_throttling_burst_limit" {
  description = "Requests allowed in a short burst above the steady rate."
  type        = number
  default     = 10

  validation {
    condition     = var.api_throttling_burst_limit > 0 && var.api_throttling_burst_limit <= 5000
    error_message = "Use 1-5000 requests (the account's default API Gateway burst)."
  }
}

# -----------------------------------------------------------------------------
# Schedules (EventBridge Scheduler, in venue_timezone)
# -----------------------------------------------------------------------------

variable "gmail_poll_schedule" {
  description = "How often the cron Lambda polls the shared Gmail inbox."
  type        = string
  default     = "rate(8 minutes)"
}

variable "daily_schedule" {
  description = "When the daily retention, stuck-job, and 120-day cleanup run starts."
  type        = string
  default     = "cron(0 7 * * ? *)"
}

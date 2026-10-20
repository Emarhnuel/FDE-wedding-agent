# AgentCore Runtime running src/runtime.py from the linux/arm64 image (HTTP protocol: port 8080,
# /invocations and /ping). The worker invokes it per job; the runtime acknowledges at once and keeps
# the session busy (add_async_task) until the job finishes. Creating it also creates the DEFAULT
# endpoint, which always points at the newest version.
resource "aws_bedrockagentcore_agent_runtime" "app" {
  # Runtime names allow only letters, digits, and underscores (up to 48 characters).
  agent_runtime_name = replace("${local.name_prefix}_runtime", "-", "_")
  description        = "Juniper Hollow ${local.environment} agent and document-indexing jobs"
  role_arn           = aws_iam_role.agentcore.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.app["agentcore"].repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  lifecycle_configuration {
    idle_runtime_session_timeout = 900   # 15 minutes idle (the default) ends a session
    max_lifetime                 = 28800 # 8 hours, the maximum, for long agent runs
  }

  # The region is set for boto3 inside the container (Secrets Manager and S3 clients).
  environment_variables = merge(local.app_environment, { AWS_DEFAULT_REGION = var.aws_region })

  # The role must already allow pulling the image when the runtime is created.
  depends_on = [aws_iam_role_policy.agentcore_runtime, aws_iam_role_policy_attachment.agentcore]
}

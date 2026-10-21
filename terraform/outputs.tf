# Read by the deploy script (terraform output -raw <name>) and shown after each apply.

output "environment" {
  description = "Workspace that was applied (dev or prod)."
  value       = local.environment
}

output "aws_region" {
  description = "Region of the stack."
  value       = var.aws_region
}

output "api_url" {
  description = "Backend API base URL; the deploy script writes it into frontend/config.js."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "website_url" {
  description = "The website on Amplify Hosting."
  value       = local.website_url
}

output "amplify_app_id" {
  description = "Amplify app for create-deployment and start-deployment."
  value       = aws_amplify_app.website.id
}

output "amplify_branch_name" {
  description = "Amplify branch the website is published to."
  value       = aws_amplify_branch.website.branch_name
}

output "ecr_lambda_repository_url" {
  description = "Push the Lambda image here (tag: the git commit SHA)."
  value       = aws_ecr_repository.app["lambda"].repository_url
}

output "ecr_agentcore_repository_url" {
  description = "Push the linux/arm64 AgentCore image here (tag: the git commit SHA)."
  value       = aws_ecr_repository.app["agentcore"].repository_url
}

output "app_secret_name" {
  description = "Secrets Manager secret to fill with the app's keys (JSON) after the first apply."
  value       = aws_secretsmanager_secret.app.name
}

output "jobs_queue_url" {
  description = "FIFO jobs queue."
  value       = aws_sqs_queue.jobs.url
}

output "jobs_dead_letter_queue_url" {
  description = "Jobs that kept failing; inspect and redrive by hand."
  value       = aws_sqs_queue.jobs_dlq.url
}

output "knowledge_bucket_name" {
  description = "Private bucket with uploaded knowledge files."
  value       = aws_s3_bucket.knowledge.bucket
}

output "agent_runtime_arn" {
  description = "AgentCore runtime the worker invokes."
  value       = aws_bedrockagentcore_agent_runtime.app.agent_runtime_arn
}

output "lambda_function_names" {
  description = "The api, worker, and cron functions."
  value       = { for name, function in aws_lambda_function.app : name => function.function_name }
}

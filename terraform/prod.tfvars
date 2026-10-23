# prod overrides, applied on top of terraform.tfvars:
#   terraform workspace select prod && terraform apply -var-file=prod.tfvars
# No secrets here: this file is committed.

# Only the prod Amplify site may call the prod API.
extra_allowed_origins = []

# Keep knowledge files and images if someone runs destroy by mistake.
force_destroy_buckets = false
log_retention_days    = 30

api_throttling_rate_limit  = 10
api_throttling_burst_limit = 20

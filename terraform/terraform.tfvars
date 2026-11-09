# dev values (loaded automatically). prod.tfvars overrides only what differs.
# No secrets here: this file is committed.

aws_region   = "us-east-1"
project_name = "juniper-hollow"

company_id        = "juniper-hollow"
venue_timezone    = "America/Chicago"
auto_send_routine = true
langsmith_project = "juniper-hollow-agent"

# Lets a local website (python -m http.server 3000) call the dev API.
extra_allowed_origins = ["http://localhost:3000"]

# dev can be torn down completely, even with uploaded files and images left in it.
force_destroy_buckets = true
log_retention_days    = 7

api_throttling_rate_limit  = 5
api_throttling_burst_limit = 10

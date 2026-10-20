# Remote state in the bucket created by terraform/bootstrap. A backend block cannot use variables, so
# the account-specific bucket name and region are passed at init time (partial configuration):
#
#   terraform init -backend-config="bucket=<state_bucket_name output>" -backend-config="region=us-east-1"
#
# Each workspace keeps its own state: dev at env:/dev/<key>, prod at env:/prod/<key>.
terraform {
  backend "s3" {
    key          = "juniper-hollow/terraform.tfstate"
    use_lockfile = true # S3 lock file; no DynamoDB table
    encrypt      = true
  }
}

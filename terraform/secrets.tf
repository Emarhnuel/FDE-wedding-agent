# One secret per environment holding the app's keys as a JSON object. Terraform creates it empty and
# never sets its value, so no key ever lands in Git or in the Terraform state. Add the value once by
# hand (Secrets Manager console, or aws secretsmanager put-secret-value), with these keys from .env:
#
#   OPENROUTER_API_KEY, INSFORGE_URL, INSFORGE_API_KEY, DATABASE_URL, HUBSPOT_PRIVATE_APP_TOKEN,
#   GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN, GMAIL_SENDER, LANGSMITH_API_KEY
#
# When the Gmail refresh token is renewed (every 7 days while the Google app is in Testing), update it here.
resource "aws_secretsmanager_secret" "app" {
  name        = "${local.name_prefix}/app"
  description = "Juniper Hollow ${local.environment} API keys and connection strings (JSON)."

  # A deleted secret's name is reserved for its recovery window. dev is deleted at once so it can be
  # rebuilt with the same name; prod keeps the 30-day window to recover from a mistaken delete.
  recovery_window_in_days = local.is_prod ? 30 : 0
}

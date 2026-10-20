"""Juniper Hollow CRM Agent package."""

import json
import os

# Deployed (Lambda and AgentCore), the API keys come from one Secrets Manager secret instead of .env:
# Lambda environment variables are limited to 4 KB and would expose the keys in the Terraform state.
# Loaded here because the package runs before any module reads os.environ. Values already set win.
if os.environ.get("APP_SECRET_ARN"):
    import boto3

    _secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["APP_SECRET_ARN"])
    for _key, _value in json.loads(_secret["SecretString"]).items():
        os.environ.setdefault(_key, str(_value))

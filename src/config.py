import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.environ.get("LANGSMITH_PROJECT", "juniper-hollow-agent")

GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_SENDER = os.environ.get("GMAIL_SENDER", "")

HUBSPOT_PRIVATE_APP_TOKEN = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN", "")

INSFORGE_API_KEY = os.environ.get("INSFORGE_API_KEY", "")
INSFORGE_URL = os.environ.get("INSFORGE_URL", "")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

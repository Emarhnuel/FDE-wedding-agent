import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "google/gemini-embedding-2")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))
CHROMA_DIRECTORY = os.environ.get("CHROMA_DIRECTORY", ".data/chroma")
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "juniper_knowledge")

CHAT_MODEL = os.environ.get("CHAT_MODEL", "mistralai/mistral-large-4-0")
CHAT_MODEL_ALTERNATIVES = [
    name.strip()
    for name in os.environ.get(
        "CHAT_MODEL_ALTERNATIVES",
        "mistralai/mistral-large-4-0,anthropic/claude-sonnet-5.5,xiaomi/mimo-v2.6-pro",
    ).split(",")
    if name.strip()
]

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

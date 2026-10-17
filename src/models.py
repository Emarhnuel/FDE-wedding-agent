from dotenv import load_dotenv
from langchain_openrouter import ChatOpenRouter

load_dotenv()

# Streaming is off: replies are drafts for staff review, never shown token by token.
# max_tokens is the output cap per call (reasoning included); each is within the model's
# OpenRouter max_completion_tokens.

# Model 1: Mistral Large 4 (max output 262,144)
model1 = ChatOpenRouter(
    model="mistralai/mistral-large-4-0",
    temperature=0.0,
    max_tokens=100_000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 2: Claude Sonnet 5.5 (max output 128,000)
model2 = ChatOpenRouter(
    model="anthropic/claude-sonnet-5.5",
    temperature=0.0,
    max_tokens=100_000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 3: Xiaomi MiMo v2.6 Pro (max output 131,072)
model3 = ChatOpenRouter(
    model="xiaomi/mimo-v2.6-pro",
    temperature=0.0,
    max_tokens=100_000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 4: Google Gemini 4 Argon (max output 65,536, so it cannot take 100,000)
model4 = ChatOpenRouter(
    model="google/gemini-4-argon",
    temperature=0.0,
    max_tokens=65_536,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

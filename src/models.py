from dotenv import load_dotenv
from langchain_openrouter import ChatOpenRouter

load_dotenv()

# Streaming is off: replies are drafts for staff review, never shown token by token.
# max_tokens is the output cap per call (reasoning included); each is within the model's
# OpenRouter max_completion_tokens.
#
# Each request is cut off after REQUEST_TIMEOUT_MS so one hung call can't use up the worker Lambda's
# 15-minute limit. Healthy calls in our traces took up to ~190 s, hence 4 minutes (ChatOpenRouter's
# timeout is in milliseconds). max_retries=1 gives the OpenRouter SDK a 150 s retry window: quick 5xx
# and connection errors are retried, but a timed-out call (always past 150 s) is not; it goes to
# ModelRetryMiddleware in agents.py instead. Don't use 0: the SDK then falls back to its own default
# of retrying timeouts for up to an hour.
REQUEST_TIMEOUT_MS = 240_000
SDK_MAX_RETRIES = 1

# Model 1: Mistral Large 4 (max output 262,144)
model1 = ChatOpenRouter(
    model="mistralai/mistral-large-4-0",
    temperature=0.0,
    max_tokens=60000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
    timeout=REQUEST_TIMEOUT_MS,
    max_retries=SDK_MAX_RETRIES,
)

# Model 2: Claude Sonnet 5.5 (max output 128,000)
model2 = ChatOpenRouter(
    model="anthropic/claude-sonnet-5.5",
    temperature=0.0,
    max_tokens=60000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
    timeout=REQUEST_TIMEOUT_MS,
    max_retries=SDK_MAX_RETRIES,
)

# Model 3: Xiaomi MiMo v2.6 Pro (max output 131,072)
model3 = ChatOpenRouter(
    model="xiaomi/mimo-v2.6-pro",
    temperature=0.0,
    max_tokens=60000,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
    timeout=REQUEST_TIMEOUT_MS,
    max_retries=SDK_MAX_RETRIES,
)

# Model 4: Google Gemini 4 Argon
model4 = ChatOpenRouter(
    model="google/gemini-4-argon",
    temperature=0.0,
    max_tokens=60036,
    streaming=False,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
    timeout=REQUEST_TIMEOUT_MS,
    max_retries=SDK_MAX_RETRIES,
)

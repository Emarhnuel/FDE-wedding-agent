from dotenv import load_dotenv
from langchain_openrouter import ChatOpenRouter

load_dotenv()

# Model 1: Mistral Large 4
model1 = ChatOpenRouter(
    model="mistralai/mistral-large-4-0",
    temperature=0.0,
    max_tokens=16384,
    streaming=True,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 2: Claude Sonnet 5.5
model2 = ChatOpenRouter(
    model="anthropic/claude-sonnet-5.5",
    temperature=0.0,
    max_tokens=16384,
    streaming=True,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 3: Xiaomi MiMo v2.6 Pro
model3 = ChatOpenRouter(
    model="xiaomi/mimo-v2.6-pro",
    temperature=0.0,
    max_tokens=16384,
    streaming=True,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# Model 4: Google Gemini 4 Argon
model4 = ChatOpenRouter(
    model="google/gemini-4-argon",
    temperature=0.0,
    max_tokens=16384,
    streaming=True,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

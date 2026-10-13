"""
Agent configurations for AI Real Estate Co-Pilot.

This module defines the supervisor agent and sub-agents for property search
and location analysis using the Deep Agents framework.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from langchain_openrouter import ChatOpenRouter
from deepagents import create_deep_agent
from deepagents.backends import StoreBackend, StateBackend, CompositeBackend
from langchain.agents.middleware import (
    ToolCallLimitMiddleware,
    ModelCallLimitMiddleware,
    ToolRetryMiddleware,
    ModelRetryMiddleware,
)
from deepagents.backends.utils import create_file_data
from deepagents.backends.protocol import WriteResult
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command
from dotenv import load_dotenv


# Load environment variables
load_dotenv()

# Configure LangSmith tracing (reads from env vars automatically)
# Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env to enable
if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    if os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT")
    print(f"[INFO] LangSmith tracing enabled for project: {os.getenv('LANGSMITH_PROJECT', 'default')}")

from src.tools import (
    browser_use_property_search_tool,
    google_places_geocode_tool,
    google_places_nearby_tool,
    make_inspection_call,
    make_negotiation_call,
    get_call_result,
    check_call_status,
    save_property_result,
    update_property_location,
    update_call_status,
    update_negotiation_status,
    finalize_property_report,
)
from src.prompts import (
    PROPERTY_SEARCH_SYSTEM_PROMPT,
    LOCATION_ANALYSIS_SYSTEM_PROMPT,
    CALL_AGENT_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT
)
from src.middleware import CallGuardrailMiddleware

# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

# OpenRouter models - used for all agents.
# Set OPENROUTER_API_KEY in .env.

# Model 1: Claude Sonnet 5 — frontier coding, agents, professional work
model1 = ChatOpenRouter(
    model="anthropic/claude-sonnet-5",
    temperature=0.0,
    max_tokens=500000,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# # Model 2: Qwen3.8 Max — multimodal reasoning flagship, agentic workflows
# model2 = ChatOpenRouter(
#     model="qwen/qwen3.8-max",
#     temperature=0.0,
#     max_tokens=16384,
#     reasoning={"effort": "high", "summary": "auto"},
#     openrouter_provider={"data_collection": "deny"},
# )

# Model 3: GLM 5.2 — long-horizon agent workflows, project-level engineering
model3 = ChatOpenRouter(
    model="z-ai/glm-5.2",
    temperature=0.0,
    max_tokens=900000,
    reasoning={"effort": "high", "summary": "auto"},
    openrouter_provider={"data_collection": "deny"},
)

# # Model 4: DeepSeek V4 Flash — fast, cheap, coding + reasoning + agents
# model4 = ChatOpenRouter(
#     model="deepseek/deepseek-v4-flash-0731",
#     temperature=0.0,
#     max_tokens=40960,
#     openrouter_provider={"data_collection": "deny"},
# )

# =============================================================================
# CONTEXT SCHEMA
# =============================================================================

@dataclass
class Context:
    """Per-run context passed on every invocation. Propagates to all subagents.

    - user_id: authenticated user (also used to scope /memories/).
    - search_id: the property_searches row id, so persistence tools can write
      per-property results back to the correct search in InsForge.

    Both default to None so that an invocation which forgets to pass context
    (e.g. a HITL resume) degrades into a handled "missing context" tool error
    instead of a TypeError that kills the whole run at context coercion. The
    frontend is still expected to send both on EVERY submit, including resumes.
    """
    user_id: str | None = None
    search_id: str | None = None


# =============================================================================
# BACKEND CONFIGURATION
# =============================================================================

# No explicit checkpointer - the LangSmith Deployment (and `langgraph dev`)
# injects a durable, Postgres-backed checkpointer automatically. This is what
# lets a paused HITL review (or a finished report) survive restarts, redeploys,
# and users returning days or weeks later on the same thread_id.

# Long-term memory files, auto-loaded into the supervisor prompt at startup.
MEMORY_FILES = ["/memories/preferences.txt", "/memories/search_history.txt"]

# -----------------------------------------------------------------------------
# Long-term memory store
# -----------------------------------------------------------------------------
# The LangSmith Deployment (and `langgraph dev` locally) provides a durable,
# Postgres-backed store automatically - we do NOT provision our own. The
# /memories/ route below writes to that runtime-provided store, scoped per user.


def _memory_namespace(rt) -> tuple[str, ...]:
    """Scope /memories/ per user so memory never leaks between users.

    Reads user_id from context_schema (primary) or server_info (auth handler
    fallback), falling back to a shared bucket for local dev.
    """
    user_id = None
    # Primary: context_schema (typed, explicit)
    ctx = getattr(rt, "context", None)
    if ctx is not None:
        user_id = getattr(ctx, "user_id", None) or (ctx.get("user_id") if isinstance(ctx, dict) else None)
    # Fallback: auth handler populates server_info.user.identity
    if not user_id:
        server_info = getattr(rt, "server_info", None)
        if server_info is not None:
            user = getattr(server_info, "user", None)
            if user is not None:
                user_id = getattr(user, "identity", None)
    return (str(user_id) if user_id else "shared",)


# Subclass StoreBackend to allow overwriting memory files without erroring.
class OverwritingStoreBackend(StoreBackend):
    def write(self, file_path: str, content: str) -> WriteResult:
        store = self._get_store()
        namespace = self._get_namespace()
        file_data = create_file_data(content)
        store_value = self._convert_file_data_to_store_value(file_data)
        store.put(namespace, file_path, store_value)
        return WriteResult(path=file_path, files_update=None)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        store = self._get_store()
        namespace = self._get_namespace()
        file_data = create_file_data(content)
        store_value = self._convert_file_data_to_store_value(file_data)
        await store.aput(namespace, file_path, store_value)
        return WriteResult(path=file_path, files_update=None)


# Route only /memories/ to durable, user-scoped storage (runtime-provided store);
# keep all other agent files (properties, locations, report) in thread-scoped state.
agent_backend = CompositeBackend(
    default=StateBackend(),
    routes={"/memories/": OverwritingStoreBackend(namespace=_memory_namespace)},
)



# =============================================================================
# AGENT FACTORY
# =============================================================================

# Property Search Sub-Agent Configuration
property_search_agent = {
    "name": "property_search",
    "description": (
        "Searches for property listings matching user criteria. "
        "Extracts detailed property data from listing pages, then saves each one "
        "via save_property_result - which pauses for human review (approve/edit/reject) "
        "before it persists. Searches and reviews ONCE, then returns whatever the "
        "user approved, even if that is fewer than the requested number."
    ),
    "system_prompt": PROPERTY_SEARCH_SYSTEM_PROMPT,
    "tools": [browser_use_property_search_tool, save_property_result],
    "model": model3,
    "interrupt_on": {
        # The review gate now sits on the tool that actually persists a
        # property, not a no-op review tool - so it can't be skipped by the
        # model simply never calling it. Calling save_property_result in
        # parallel for multiple properties batches them into ONE interrupt
        # (per LangChain docs: multiple pending actions -> single interrupt
        # with an actionRequests array).
        "save_property_result": {
            "allowed_decisions": ["approve", "edit", "reject"]
        }
    },
    "middleware": [
        # Runaway guardrail on the save/review batch. There is only ONE review
        # round now, but the batch size is the user's requested property count,
        # which has a minimum of 3 and NO maximum (see desired_results in the
        # search forms). This ceiling must therefore stay comfortably above any
        # realistic request: if it were hit mid-batch, exit_behavior="continue"
        # means the remaining saves are silently dropped and those properties
        # never reach the review card or the database.
        # thread_limit because HITL resumes are separate invocations on the
        # same thread.
        ToolCallLimitMiddleware(
            tool_name="save_property_result",
            thread_limit=40,
            exit_behavior="continue",
        ),
        # Cap the expensive Browser Use search: one search plus its retries
        # (max_retries=4 below, so 5 calls in the normal worst case) - there
        # is no second/top-up round. Keep this in sync with the number quoted
        # in PROPERTY_SEARCH_SYSTEM_PROMPT's <Hard Limits>.
        ToolCallLimitMiddleware(
            tool_name="browser_use_property_search_tool",
            thread_limit=8,
            exit_behavior="continue",
        ),
        # Retry only the (idempotent) search tool on transient failures.
        # Keep retries low - each Browser Use run costs money and, with the
        # v4 SDK's realistic per-attempt timeout (20 min, vs the old v2
        # default of 300s that was firing spuriously), a retry now almost
        # always means a genuine failure rather than a premature timeout.
        ToolRetryMiddleware(
            tools=["browser_use_property_search_tool"],
            max_retries=4,
        ),
        # Transient model/provider error resilience.
        ModelRetryMiddleware(max_retries=9),
    ],
    # NOTE: no response_format here. PropertySearchResults gave the model an
    # escape hatch to satisfy its output contract by emitting JSON directly,
    # bypassing every tool call (including the HITL gate) entirely - this is
    # exactly what happened when browser_use_property_search_tool failed.
}

# Location Analysis Sub-Agent Configuration
location_analysis_agent = {
    "name": "location_analysis",
    "description": "Analyzes property locations and nearby amenities. Saves analysis to /locations/ using write_file.",
    "system_prompt": LOCATION_ANALYSIS_SYSTEM_PROMPT,
    "tools": [google_places_geocode_tool, google_places_nearby_tool, update_property_location],
    "model": model3,
    "middleware": [
        # Google Places calls are idempotent - safe to retry on transient errors.
        ToolRetryMiddleware(
            tools=["google_places_geocode_tool", "google_places_nearby_tool"],
            max_retries=6,
        ),
        ModelRetryMiddleware(max_retries=5),
    ],
}


# Call Agent Sub-Agent Configuration
call_agent = {
    "name": "call_agent",
    "description": "Makes voice AI calls to property agents for inspections or to owners for acquisition negotiations using Retell AI.",
    "system_prompt": CALL_AGENT_SYSTEM_PROMPT,
    "tools": [make_inspection_call, make_negotiation_call, get_call_result, check_call_status, update_call_status, update_negotiation_status],
    "model": model3,
    "middleware": [
        # Deterministic guardrail: hard-blocks a call-placement tool if the
        # per-property cap is reached (3 negotiation / 7 inspection) or the
        # contact's local time is outside 07:00-19:00. Runs FIRST (outermost)
        # so a blocked call never reaches Retell. Counts come from InsForge, so
        # the cap holds even across the separate threads the cron spawns.
        CallGuardrailMiddleware(),
        # Retry ONLY the idempotent read tools. NEVER retry the call-placement
        # tools (make_inspection_call / make_negotiation_call) - retrying them
        # would re-dial and place duplicate phone calls.
        ToolRetryMiddleware(
            tools=["get_call_result", "check_call_status"],
            max_retries=3,
        ),
        ModelRetryMiddleware(max_retries=4),
    ],
}

 
# Supervisor's system prompt is long and identical on every model call across
# a thread (up to 60 of them per ModelCallLimitMiddleware below) - marking it
# ephemeral lets OpenRouter/Anthropic cache it after the first call, so every
# call after that reads from cache instead of paying full price to re-send it.
_supervisor_system_message = SystemMessage(
    content=[{"type": "text", "text": SUPERVISOR_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
)

# Create the supervisor agent
supervisor = create_deep_agent(
    model=model1,
    system_prompt=_supervisor_system_message,
    subagents=[property_search_agent, location_analysis_agent, call_agent],
    # finalize_property_report is SUPERVISOR-ONLY - it authors the "what to do
    # next" advice and marks a property's report as ready. call_agent does not
    # get this tool: it only persists raw call facts via update_call_status /
    # update_negotiation_status.
    tools=[finalize_property_report],
    memory=MEMORY_FILES,
    context_schema=Context,
    middleware=[
        # Runaway/cost guardrail on the orchestrator's own model calls.
        ModelCallLimitMiddleware(thread_limit=60, run_limit=30, exit_behavior="end"),
        # Transient model/provider error resilience.
        ModelRetryMiddleware(max_retries=2),
    ],
    backend=agent_backend,
    # No `checkpointer=` and no `store=` — the LangSmith Deployment (and `langgraph dev`) injects a
    # durable, Postgres-backed store at runtime. The /memories/ route writes to it.
)

print("[INFO] Supervisor agent created successfully")

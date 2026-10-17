"""
Agent configurations for Juniper Hollow Weddings & Events.

This module defines the supervisor agent and sub-agents for knowledge retrieval,
wedding inquiry drafting, and couple retention outreach using the Deep Agents framework.
"""

import os
from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import WriteResult
from deepagents.backends.utils import create_file_data
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

# Load environment variables
load_dotenv()

# Configure LangSmith tracing (reads from env vars automatically)
# Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env to enable
if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    if os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT")
    print(f"[INFO] LangSmith tracing enabled for project: {os.getenv('LANGSMITH_PROJECT', 'juniper-hollow-agent')}")

from src.models import model1, model2, model3, model4
from src.prompts import (
    INQUIRY_SYSTEM_PROMPT,
    KNOWLEDGE_RETRIEVER_SYSTEM_PROMPT,
    RETENTION_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
)
from src.schemas import AgentContext, InquiryDraft, KnowledgeEvidence, RetentionDraft
from src.middleware import (
    DELIVERY_INTERRUPT_CONFIG,
    DeliveryGuardrailMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
    sanitize_tool_error,
)
from src.tools.inquiry_tools import get_inquiry_history, save_inquiry_draft
from src.tools.knowledge_tools import search_company_knowledge
from src.tools.retention_tools import get_couple_history, save_retention_draft
from src.tools.supervisor_tools import hand_off_to_staff, send_customer_email, send_retention_email

# Staff review every draft before it is saved, in addition to the guarded send tools.
DRAFT_INTERRUPT_CONFIG = {"allowed_decisions": ["approve", "edit", "reject"]}


# =============================================================================
# BACKEND CONFIGURATION
# =============================================================================

# Long-term memory files, auto-loaded into the supervisor prompt at startup.
MEMORY_FILES = ["/memories/preferences.txt", "/memories/interaction_history.txt"]


def _memory_namespace(rt) -> tuple[str, ...]:
    """Scope /memories/ per company and contact so memory never leaks across customers."""
    company_id = "juniper-hollow"
    contact_id = "shared"
    ctx = getattr(rt, "context", None)
    if ctx is not None:
        company_id = getattr(ctx, "company_id", None) or (
            ctx.get("company_id") if isinstance(ctx, dict) else None
        ) or company_id
        contact_id = getattr(ctx, "contact_id", None) or (
            ctx.get("contact_id") if isinstance(ctx, dict) else None
        ) or contact_id
    return (str(company_id), str(contact_id))


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


# Route only /memories/ to durable, company/contact-scoped storage;
# keep all other agent files in thread-scoped state.
agent_backend = CompositeBackend(
    default=StateBackend(),
    routes={"/memories/": OverwritingStoreBackend(namespace=_memory_namespace)},
)


# =============================================================================
# AGENT FACTORY
# =============================================================================

# Knowledge Retriever Sub-Agent Configuration
knowledge_retriever = {
    "name": "knowledge_retriever",
    "description": (
        "Searches published Juniper Hollow company knowledge in ChromaDB for venue capacities, "
        "amenities, catering, rules, FAQs, and internal reference policies. Read-only."
    ),
    "system_prompt": KNOWLEDGE_RETRIEVER_SYSTEM_PROMPT,
    "tools": [search_company_knowledge],
    "model": model1,
    "response_format": ToolStrategy(KnowledgeEvidence),
    "middleware": [
        ToolCallLimitMiddleware(
            tool_name="search_company_knowledge",
            run_limit=6,
            exit_behavior="continue",
        ),
        ToolRetryMiddleware(
            tools=["search_company_knowledge"],
            max_retries=3,
        ),
        ToolErrorMiddleware(on_error=sanitize_tool_error),
        ModelRetryMiddleware(max_retries=3),
    ],
}

# Inquiry Sub-Agent Configuration
inquiry_agent = {
    "name": "inquiry_agent",
    "description": (
        "Extracts wedding inquiry details, reads scoped customer history, and prepares "
        "a grounded email draft with source citations and review flags. Does not send email."
    ),
    "system_prompt": INQUIRY_SYSTEM_PROMPT,
    "tools": [get_inquiry_history, save_inquiry_draft],
    "model": model1,
    # The supervisor must send the saved draft exactly, so it gets exact fields, not prose.
    "response_format": ToolStrategy(InquiryDraft),
    "interrupt_on": {"save_inquiry_draft": DRAFT_INTERRUPT_CONFIG},
    "middleware": [
        ToolCallLimitMiddleware(
            tool_name="save_inquiry_draft",
            run_limit=3,
            exit_behavior="continue",
        ),
        ToolRetryMiddleware(
            tools=["get_inquiry_history"],
            max_retries=3,
        ),
        ToolErrorMiddleware(on_error=sanitize_tool_error),
        ModelRetryMiddleware(max_retries=3),
    ],
}

# Retention Sub-Agent Configuration
retention_agent = {
    "name": "retention_agent",
    "description": (
        "Prepares warm anniversary or referral outreach drafts for eligible past couples "
        "selected by the backend. All retention drafts require staff approval."
    ),
    "system_prompt": RETENTION_SYSTEM_PROMPT,
    "tools": [get_couple_history, save_retention_draft],
    "model": model1,
    "response_format": ToolStrategy(RetentionDraft),
    "interrupt_on": {"save_retention_draft": DRAFT_INTERRUPT_CONFIG},
    "middleware": [
        ToolCallLimitMiddleware(
            tool_name="save_retention_draft",
            run_limit=2,
            exit_behavior="continue",
        ),
        ToolRetryMiddleware(
            tools=["get_couple_history"],
            max_retries=3,
        ),
        ToolErrorMiddleware(on_error=sanitize_tool_error),
        ModelRetryMiddleware(max_retries=3),
    ],
}

# Supervisor's system prompt is long and identical on every model call across a thread;
# marking it ephemeral lets OpenRouter cache it after the first call.
_supervisor_system_message = SystemMessage(
    content=[{"type": "text", "text": SUPERVISOR_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
)

def build_supervisor(checkpointer: BaseCheckpointSaver | None = None, store: BaseStore | None = None):
    """Create the supervisor. interrupt_on requires a checkpointer and /memories/ requires a store;
    runtime.py passes Postgres-backed ones, and the in-memory defaults are for local runs and tests."""
    return create_deep_agent(
        model=model1,
        system_prompt=_supervisor_system_message,
        subagents=[knowledge_retriever, inquiry_agent, retention_agent],
        tools=[send_customer_email, send_retention_email, hand_off_to_staff],
        interrupt_on={
            "send_customer_email": DELIVERY_INTERRUPT_CONFIG,
            "send_retention_email": {
                "allowed_decisions": ["approve", "edit", "reject"],
            },
        },
        memory=MEMORY_FILES,
        context_schema=AgentContext,
        middleware=[
            DeliveryGuardrailMiddleware(),
            ModelCallLimitMiddleware(thread_limit=40, run_limit=20, exit_behavior="end"),
            ModelRetryMiddleware(max_retries=2),
            ToolErrorMiddleware(on_error=sanitize_tool_error),
        ],
        backend=agent_backend,
        checkpointer=checkpointer or InMemorySaver(),
        store=store or InMemoryStore(),
    )


supervisor = build_supervisor()

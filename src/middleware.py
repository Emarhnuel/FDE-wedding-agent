import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command
from langsmith.anonymizer import create_anonymizer

SENSITIVE_PATTERN = re.compile(
    r"(\$\s?\d|"
    r"\b(?:price|pricing|quote|rates?|cost|fees?|discounts?|retainer|deposit|"
    r"date\s+hold|hold\s+a\s+date|book(?:ing)?|reserv(?:e|ation)|availab(?:le|ility)|"
    r"contracts?|agreements?|cancel(?:lation)?|refunds?|complaints?|disputes?)\b)",
    re.IGNORECASE,
)

GUARDED_DELIVERY_TOOLS = frozenset({"send_customer_email", "send_retention_email"})


def requires_human_review(text: str, *, job_type: str = "inquiry") -> bool:
    """Return True when the job type or text requires mandatory staff review."""
    if job_type == "retention":
        return True
    return bool(SENSITIVE_PATTERN.search(text or ""))


def _extract_combined_text(request: ToolCallRequest) -> str:
    args = request.tool_call.get("args") or {}
    parts = [
        str(args.get(key, ""))
        for key in ("subject", "body", "inquiry_text", "topic", "notes")
        if args.get(key)
    ]
    state = request.state or {}
    for msg in state.get("messages", []) if isinstance(state, dict) else []:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def should_interrupt_delivery(request: ToolCallRequest) -> bool:
    """Conditional HITL predicate for guarded outbound email tools.

    Fails closed (returns True to pause for staff review) whenever the context
    is missing, the job is retention outreach, or any restricted topic appears.
    """
    ctx = getattr(request.runtime, "context", None)
    if ctx is None or not getattr(ctx, "company_id", None):
        return True
    job_type = getattr(ctx, "job_type", "inquiry")
    if getattr(ctx, "requires_approval", False):
        return True
    args = request.tool_call.get("args") or {}
    if args.get("requires_review") is True:
        return True
    return requires_human_review(_extract_combined_text(request), job_type=job_type)


DELIVERY_INTERRUPT_CONFIG: InterruptOnConfig = {
    "allowed_decisions": ["approve", "edit", "reject"],
    "when": should_interrupt_delivery,
}


def sanitize_tool_error(exc: Exception, request: ToolCallRequest) -> str | None:
    """Return a sanitized error message for ValueError without leaking internals."""
    if isinstance(exc, ValueError):
        return f"Tool `{request.tool_call['name']}` failed validation: {exc}"
    return None


class DeliveryGuardrailMiddleware(AgentMiddleware):
    """Deterministic action-boundary guardrail for outbound email and retention tools."""

    def __init__(self, guarded_tools: set[str] | frozenset[str] = GUARDED_DELIVERY_TOOLS):
        super().__init__()
        self.guarded_tools = frozenset(guarded_tools)

    def _validate(self, request: ToolCallRequest) -> str | None:
        tool_name = request.tool_call.get("name", "")
        ctx = getattr(request.runtime, "context", None)
        company_id = getattr(ctx, "company_id", None) if ctx is not None else None
        if not company_id:
            return f"Blocked `{tool_name}`: missing server-verified company_id in runtime context."

        if tool_name not in self.guarded_tools:
            return None

        args = request.tool_call.get("args") or {}
        if not args.get("recipient"):
            return f"Blocked `{tool_name}`: recipient email is required."

        job_type = getattr(ctx, "job_type", "retention" if tool_name == "send_retention_email" else "inquiry")
        if job_type == "retention" or tool_name == "send_retention_email":
            if not getattr(ctx, "marketing_consent", False):
                return f"Blocked `{tool_name}`: contact does not have verified marketing consent."
            if getattr(ctx, "cooldown_active", False):
                return f"Blocked `{tool_name}`: contact is within the outreach cooldown window."

        if getattr(ctx, "source_superseded", False):
            return f"Blocked `{tool_name}`: draft cites a superseded knowledge version."

        needs_review = should_interrupt_delivery(request)
        staff_approved = bool(getattr(ctx, "staff_approved", False))
        if needs_review and not staff_approved:
            return (
                f"Blocked `{tool_name}`: message requires staff approval before sending "
                "(sensitive topic or retention outreach)."
            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if error := self._validate(request):
            return ToolMessage(
                content=error,
                tool_call_id=request.tool_call["id"],
                name=request.tool_call.get("name"),
                status="error",
            )
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if error := self._validate(request):
            return ToolMessage(
                content=error,
                tool_call_id=request.tool_call["id"],
                name=request.tool_call.get("name"),
                status="error",
            )
        return await handler(request)


class OpenRouterPromptCachingMiddleware(AgentMiddleware):
    """Mark the system prompt as an OpenRouter cache breakpoint on every model call.

    OpenRouter caches only content blocks carrying `cache_control`; Deep Agents' built-in
    caching middleware targets native Anthropic/Bedrock models and skips ChatOpenRouter.
    Deep Agents places custom middleware after its own prompt-building middleware, so the
    breakpoint covers the full static prompt; per-contact memory is appended after it.
    """

    def _with_breakpoint(self, request: ModelRequest) -> ModelRequest:
        message = request.system_message
        if message is None:
            return request
        content = message.content
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [
            {"type": "text", "text": block} if isinstance(block, str) else dict(block) for block in content
        ]
        if not blocks:
            return request
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        return request.override(system_message=SystemMessage(content=blocks))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._with_breakpoint(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._with_breakpoint(request))


# Payment and identity numbers the venue never needs from an email. Labeled patterns require
# a digit, so ordinary words after "account" or "passport" are left alone.
SENSITIVE_NUMBER_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "iban": r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b",
    "bank_account": r"(?i)\b(?:account|acct|routing|aba|sort code)(?: number| no\.?| #)?[\s:#]*\d[\d -]{5,20}\d\b",
    "id_document": (
        r"(?i)\b(?:passport|driver'?s? licen[cs]e|national id|id card)(?: number| no\.?| #)?[\s:#]*"
        r"(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,19}\b"
    ),
}


def build_pii_middleware() -> list[PIIMiddleware]:
    """Redact card, bank, and ID numbers in the customer message before any model sees them.

    Redaction rewrites the stored message, so the numbers never reach the model provider,
    the checkpoints, or the subagents. Names, emails, phones, and dates are kept: the agent
    needs them to reply.
    """
    return [
        PIIMiddleware("credit_card", strategy="redact"),
        *(PIIMiddleware(name, detector=pattern, strategy="redact") for name, pattern in SENSITIVE_NUMBER_PATTERNS.items()),
    ]


# Customer contact details and sensitive numbers are masked in LangSmith traces only;
# the agent itself still works with the real values.
# Rules apply in order: the specific number patterns run before the generic card pattern.
TRACE_ANONYMIZER = create_anonymizer(
    [
        *({"pattern": re.compile(pattern), "replace": f"[{name.upper()}]"} for name, pattern in SENSITIVE_NUMBER_PATTERNS.items()),
        {"pattern": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "replace": "[EMAIL]"},
        {"pattern": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)"), "replace": "[PHONE]"},
        {"pattern": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "replace": "[CARD]"},
    ]
)


__all__ = [
    "DELIVERY_INTERRUPT_CONFIG",
    "DeliveryGuardrailMiddleware",
    "GUARDED_DELIVERY_TOOLS",
    "ModelCallLimitMiddleware",
    "ModelRetryMiddleware",
    "OpenRouterPromptCachingMiddleware",
    "SENSITIVE_NUMBER_PATTERNS",
    "TRACE_ANONYMIZER",
    "ToolCallLimitMiddleware",
    "ToolErrorMiddleware",
    "ToolRetryMiddleware",
    "build_pii_middleware",
    "requires_human_review",
    "sanitize_tool_error",
    "should_interrupt_delivery",
]

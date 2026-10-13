import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

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


__all__ = [
    "DELIVERY_INTERRUPT_CONFIG",
    "DeliveryGuardrailMiddleware",
    "GUARDED_DELIVERY_TOOLS",
    "ModelCallLimitMiddleware",
    "ModelRetryMiddleware",
    "ToolCallLimitMiddleware",
    "ToolErrorMiddleware",
    "ToolRetryMiddleware",
    "requires_human_review",
    "sanitize_tool_error",
    "should_interrupt_delivery",
]

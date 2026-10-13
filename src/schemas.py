from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class AgentContext:
    """Server-verified runtime context passed to agents, tools, and middleware."""

    company_id: str = "juniper-hollow"
    contact_id: str | None = None
    conversation_id: str | None = None
    job_type: Literal["inquiry", "retention"] = "inquiry"
    requires_approval: bool = False
    staff_approved: bool = False
    marketing_consent: bool = False
    cooldown_active: bool = False
    source_superseded: bool = False


class SourceCitation(BaseModel):
    """Approved knowledge-base source citation."""

    source_id: str = Field(description="Document ID, e.g. JH-KB-2026-03")
    version: str = Field(description="Published version, e.g. 3.0")
    page: int | None = Field(default=None, description="Page number in the source document")
    venue: str | None = Field(default=None, description="Venue name if venue-specific")


class KnowledgeEvidence(BaseModel):
    """Structured evidence packet returned by the knowledge retriever subagent."""

    supported_facts: list[str] = Field(default_factory=list, description="Facts backed by retrieved passages")
    sources: list[SourceCitation] = Field(default_factory=list, description="Exact source citations used")
    missing_answers: list[str] = Field(default_factory=list, description="Questions not covered by approved sources")
    review_reasons: list[str] = Field(default_factory=list, description="Sensitive topics requiring staff review")


class InquiryDetails(BaseModel):
    """Customer-supplied details extracted from a wedding inquiry."""

    couple_names: str | None = Field(default=None, description="Names supplied by the couple")
    email: str | None = Field(default=None, description="Customer email address")
    phone: str | None = Field(default=None, description="Customer phone number")
    venue: str | None = Field(default=None, description="Requested venue name if stated")
    preferred_date: str | None = Field(default=None, description="Requested wedding date or season")
    guest_count: int | None = Field(default=None, description="Estimated guest count if stated")
    budget: str | None = Field(default=None, description="Stated budget if provided")
    questions: list[str] = Field(default_factory=list, description="Questions asked by the customer")


class InquiryDraft(BaseModel):
    """Structured output for the inquiry drafting subagent."""

    extracted_details: InquiryDetails = Field(description="Customer details extracted from the inquiry")
    subject: str = Field(description="Proposed email subject line")
    body: str = Field(description="Proposed customer-facing email body without internal IDs or citations")
    sources: list[SourceCitation] = Field(default_factory=list, description="Approved sources supporting the draft")
    requires_review: bool = Field(description="True if any sensitive topic or evidence gap requires staff review")
    review_reasons: list[str] = Field(default_factory=list, description="Reasons staff review is required")
    unresolved_questions: list[str] = Field(default_factory=list, description="Questions needing staff follow-up")


class RetentionDraft(BaseModel):
    """Structured output for the retention and referral outreach subagent."""

    occasion: Literal["anniversary", "referral", "follow_up"] = Field(description="Verified outreach occasion")
    subject: str = Field(description="Proposed email subject line")
    body: str = Field(description="Warm, low-pressure outreach email body")
    sources: list[SourceCitation] = Field(default_factory=list, description="Approved sources cited, if any")
    requires_review: bool = Field(default=True, description="Retention messages always require staff review")
    review_reasons: list[str] = Field(default_factory=list, description="Reasons staff review is required")
    unresolved_questions: list[str] = Field(default_factory=list, description="Any blockers or missing details")


class WorkflowResult(BaseModel):
    """Final internal workflow result returned by the supervisor agent."""

    outcome: Literal["drafted", "pending_review", "rejected", "handed_off", "sent"] = Field(
        description="Current workflow status; only 'sent' indicates confirmed delivery"
    )
    draft_id: str | None = Field(default=None, description="Saved draft reference ID if created")
    subject: str | None = Field(default=None, description="Draft email subject")
    body: str | None = Field(default=None, description="Draft email body")
    sources: list[SourceCitation] = Field(default_factory=list, description="Supporting source citations")
    review_reasons: list[str] = Field(default_factory=list, description="Reasons for staff review or handoff")
    unresolved_questions: list[str] = Field(default_factory=list, description="Open questions for staff")

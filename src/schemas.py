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

    source_id: str = Field(description="Document ID exactly as returned by the search tool, e.g. JH-KB-2026-03")
    version: str = Field(description="Published version exactly as returned by the search tool, e.g. 3.0")
    location: str | None = Field(default=None, description="Where in the document, e.g. 'p.4 FAQ, Tours'")
    venue: str | None = Field(default=None, description="Venue name if the fact is venue-specific")


class KnowledgeEvidence(BaseModel):
    """Evidence packet returned by the knowledge retriever subagent."""

    supported_facts: list[str] = Field(default_factory=list, description="Facts backed by retrieved passages, with any conditions")
    sources: list[SourceCitation] = Field(default_factory=list, description="Exact source citations for the supported facts")
    missing_answers: list[str] = Field(default_factory=list, description="Questions not answered by approved sources, or with conflicting sources")
    review_reasons: list[str] = Field(default_factory=list, description="Restricted topics in the request that need staff review")


class InquiryDraft(BaseModel):
    """Saved inquiry reply draft, returned by the inquiry subagent to the supervisor."""

    draft_id: str | None = Field(description="draft_id from the confirmed save_inquiry_draft result; null if not saved")
    status: Literal["pending_review", "drafted", "not_saved"] = Field(
        description="Status from the confirmed save result, or not_saved if the save failed"
    )
    recipient: str = Field(description="Customer email address exactly as saved")
    subject: str = Field(default="", description="Subject exactly as saved")
    body: str = Field(default="", description="Email body exactly as saved")
    sources: list[SourceCitation] = Field(default_factory=list, description="Approved sources supporting the draft")
    requires_review: bool = Field(description="requires_review value reported by the save result")
    review_reasons: list[str] = Field(default_factory=list, description="Reasons staff review is required")
    unresolved_questions: list[str] = Field(
        default_factory=list, description="Evidence gaps, save errors, or questions for staff follow-up"
    )


class RetentionDraft(BaseModel):
    """Saved retention outreach draft, returned by the retention subagent to the supervisor."""

    draft_id: str | None = Field(description="draft_id from the confirmed save_retention_draft result; null if not saved")
    status: Literal["pending_review", "not_saved"] = Field(
        description="pending_review once saved; not_saved if the couple is blocked, staff rejected the save, or it failed"
    )
    occasion: Literal["anniversary", "referral", "follow_up"] = Field(description="Outreach occasion from the task")
    recipient: str = Field(default="", description="Couple's email address exactly as saved")
    subject: str = Field(default="", description="Subject exactly as saved, including staff edits")
    body: str = Field(default="", description="Email body exactly as saved, including staff edits")
    sources: list[SourceCitation] = Field(default_factory=list, description="Approved sources cited, if any")
    review_reasons: list[str] = Field(default_factory=list, description="Reasons staff review is required")
    unresolved_questions: list[str] = Field(
        default_factory=list, description="Eligibility blockers, staff rejection messages, or missing details"
    )

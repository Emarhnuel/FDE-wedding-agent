from datetime import UTC, datetime, timedelta
from typing import Any

from langchain.tools import ToolRuntime, tool

from .. import database
from ..schemas import AgentContext
from .inquiry_tools import get_hubspot_contact

VALID_OCCASIONS = frozenset({"anniversary", "referral", "follow_up"})


def get_couple_record(contact_id: str, company_id: str) -> dict[str, Any] | None:
    """Fetch a couple's structured retention record from InsForge scoped by company."""
    rows = database.query_records(
        "couples",
        company_id=f"eq.{company_id}",
        contact_id=f"eq.{contact_id.strip()}",
        limit=1,
    )
    return rows[0] if rows else None


def check_couple_eligibility(
    couple: dict[str, Any],
    *,
    company_id: str,
    cooldown_days: int = 90,
) -> tuple[bool, list[str]]:
    """Evaluate all retention suppression rules and return (is_eligible, blockers)."""
    blockers: list[str] = []
    if not couple.get("marketing_consent"):
        blockers.append("missing_marketing_consent")
    if couple.get("do_not_contact"):
        blockers.append("do_not_contact_flag")
    if couple.get("has_open_complaint"):
        blockers.append("open_complaint")

    last_sent = couple.get("last_outreach_at")
    if last_sent:
        cutoff = datetime.now(UTC) - timedelta(days=cooldown_days)
        sent_dt = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00"))
        if sent_dt > cutoff:
            blockers.append("within_cooldown_window")

    pending = database.query_records(
        "email_drafts",
        company_id=f"eq.{company_id}",
        contact_id=f"eq.{couple['contact_id']}",
        job_type="eq.retention",
        status="eq.pending_review",
        limit=1,
    )
    if pending:
        blockers.append("existing_pending_retention_draft")

    return (len(blockers) == 0, blockers)


@tool
def get_couple_history(
    contact_id: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Read verified past wedding details, consent, eligibility, and outreach history for a couple."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")

    couple = get_couple_record(contact_id, company_id)
    if not couple:
        return f"No verified couple record found for contact_id={contact_id}. Cannot draft retention outreach."

    eligible, blockers = check_couple_eligibility(couple, company_id=company_id)
    lines = [
        f"Couple [contact_id={couple['contact_id']}]: names={couple['couple_names']}, "
        f"email={couple['email']}, venue={couple.get('venue') or 'unknown'}, "
        f"wedding_date={couple.get('wedding_date') or 'unknown'}",
        f"Eligibility: {'ELIGIBLE' if eligible else 'BLOCKED (' + ', '.join(blockers) + ')'}",
    ]

    try:
        hs_contact = get_hubspot_contact(couple["email"])
    except Exception:
        hs_contact = None
    if hs_contact:
        props = hs_contact.get("properties", {})
        lines.append(
            f"HubSpot Contact [id={hs_contact['id']}]: stage={props.get('lifecyclestage') or 'unknown'}"
        )

    drafts = database.query_records(
        "email_drafts",
        company_id=f"eq.{company_id}",
        contact_id=f"eq.{couple['contact_id']}",
        job_type="eq.retention",
        order="created_at.desc",
        limit=5,
    )
    if drafts:
        lines.append("Prior retention drafts:")
        for d in drafts:
            lines.append(f"- [draft_id={d['id']}, status={d['status']}] Subject: {d['subject']}")
    else:
        lines.append("No prior retention drafts on record.")

    return "\n".join(lines)


@tool
def save_retention_draft(
    contact_id: str,
    recipient: str,
    occasion: str,
    subject: str,
    body: str,
    runtime: ToolRuntime[AgentContext],
    sources: list[dict[str, Any]] | None = None,
    review_reasons: list[str] | None = None,
) -> str:
    """Save a retention or referral outreach draft for staff review after verifying couple eligibility."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")

    normalized_occasion = occasion.strip().lower()
    if normalized_occasion not in VALID_OCCASIONS:
        raise ValueError(f"Invalid occasion '{occasion}'. Must be one of: {', '.join(sorted(VALID_OCCASIONS))}.")

    couple = get_couple_record(contact_id, company_id)
    if not couple:
        raise ValueError(f"Cannot save retention draft: no verified couple record for contact_id={contact_id}.")

    eligible, blockers = check_couple_eligibility(couple, company_id=company_id)
    if not eligible:
        raise ValueError(f"Cannot save retention draft for {contact_id}; blocked by: {', '.join(blockers)}.")

    reasons = list(review_reasons or [])
    if f"retention:{normalized_occasion}" not in reasons:
        reasons.append(f"retention:{normalized_occasion}")

    thread_id = runtime.context.conversation_id or f"retention:{contact_id}:{normalized_occasion}"
    draft = database.save_email_draft(
        company_id=company_id,
        thread_id=thread_id,
        contact_id=couple["contact_id"],
        recipient=recipient.strip() or couple["email"],
        subject=subject.strip(),
        body=body.strip(),
        job_type="retention",
        sources=sources or [],
        requires_review=True,
        review_reasons=reasons,
        status="pending_review",
    )
    return (
        f"Saved retention draft [draft_id={draft['id']}, contact_id={couple['contact_id']}, "
        f"occasion={normalized_occasion}, status=pending_review, requires_review=True]"
    )

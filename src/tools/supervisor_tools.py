"""Supervisor agent tools including guarded email delivery and review flow."""

import base64
import os
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain.tools import ToolRuntime, tool

from .. import database
from ..schemas import AgentContext
from .inquiry_tools import HUBSPOT_BASE_URL, _hubspot_headers, get_hubspot_contact
from .retention_tools import check_couple_eligibility, get_couple_record

load_dotenv()

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
AGENT_ACTOR_ID = "agent:supervisor"

# Statuses that permanently block delivery of a draft.
BLOCKED_STATUSES = frozenset({"rejected", "sending", "sent", "delivery_uncertain", "send_failed", "handed_off"})


# =============================================================================
# GMAIL AND HUBSPOT HELPERS
# =============================================================================

def get_gmail_access_token() -> str:
    """Exchange the stored Gmail OAuth refresh token for a short-lived access token."""
    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError("GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN must be set in .env")
    response = httpx.post(
        GMAIL_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def send_gmail_message(access_token: str, *, recipient: str, subject: str, body: str) -> dict[str, Any]:
    """Send a plain-text email from the shared Gmail inbox and return Gmail's message record."""
    sender = os.environ.get("GMAIL_SENDER", "")
    if not sender:
        raise RuntimeError("GMAIL_SENDER is not set in .env")
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    response = httpx.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def record_hubspot_email_note(recipient: str, subject: str, body: str) -> str | None:
    """Log a sent email as a note on the recipient's HubSpot contact; returns the note ID."""
    contact = get_hubspot_contact(recipient)
    if not contact:
        return None
    response = httpx.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes",
        headers=_hubspot_headers(),
        json={
            "properties": {
                "hs_timestamp": datetime.now(UTC).isoformat(),
                "hs_note_body": f"AI reply sent — Subject: {subject}\n\n{body}",
            },
            "associations": [
                {
                    "to": {"id": contact["id"]},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }
            ],
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json().get("id")


# =============================================================================
# DELIVERY CHECKS
# =============================================================================

def find_superseded_sources(sources: list[dict[str, Any]], company_id: str) -> list[str]:
    """Return cited sources that are not the currently published version (fails closed if unknown)."""
    superseded: list[str] = []
    for source in sources:
        source_id = str(source.get("source_id") or "")
        version = str(source.get("version") or "")
        published = database.query_records(
            "documents",
            company_id=f"eq.{company_id}",
            source_id=f"eq.{source_id}",
            status="eq.published",
            limit=1,
        )
        if not published or published[0]["version"] != version:
            superseded.append(f"{source_id} v{version}")
    return superseded


def validate_draft_for_delivery(
    draft: dict[str, Any] | None,
    *,
    draft_id: str,
    job_type: str,
    recipient: str,
    subject: str,
    body: str,
    context: AgentContext,
) -> None:
    """Raise ValueError unless the stored draft may be sent exactly as requested."""
    if not draft:
        raise ValueError(f"No draft found for draft_id={draft_id}.")
    if draft["job_type"] != job_type:
        raise ValueError(f"Draft {draft_id} is a {draft['job_type']} draft, not {job_type}.")
    if draft["status"] in BLOCKED_STATUSES:
        raise ValueError(f"Draft {draft_id} cannot be sent from status={draft['status']}.")

    # Delivery uses the stored draft; arguments that differ mean an unapproved edit.
    if (recipient.strip(), subject.strip(), body.strip()) != (
        draft["recipient"].strip(),
        draft["subject"].strip(),
        draft["body"].strip(),
    ):
        raise ValueError(
            f"Recipient, subject, or body differs from stored draft {draft_id}; "
            "edits must be saved and approved in the staff portal before sending."
        )

    needs_review = job_type == "retention" or draft["requires_review"]
    if needs_review and not (
        draft["status"] == "approved" and draft.get("reviewed_by") and context.staff_approved
    ):
        raise ValueError(f"Draft {draft_id} requires recorded staff approval before sending.")
    if not needs_review and draft["status"] not in {"drafted", "approved"}:
        raise ValueError(f"Draft {draft_id} is not ready to send (status={draft['status']}).")

    superseded = find_superseded_sources(draft.get("sources") or [], context.company_id)
    if superseded:
        raise ValueError(
            f"Draft {draft_id} cites superseded or unpublished sources ({', '.join(superseded)}); regenerate it."
        )


def claim_draft_for_sending(draft: dict[str, Any], company_id: str) -> bool:
    """Atomically move a draft into 'sending' so duplicate events cannot send it twice."""
    rows = database.update_records(
        "email_drafts",
        {"status": "sending"},
        id=f"eq.{draft['id']}",
        company_id=f"eq.{company_id}",
        status=f"eq.{draft['status']}",
    )
    return bool(rows)


def deliver_claimed_draft(draft: dict[str, Any], company_id: str, access_token: str) -> str:
    """Send a claimed draft, record the outcome, and never retry an uncertain send."""
    draft_id = draft["id"]
    try:
        gmail_message = send_gmail_message(
            access_token,
            recipient=draft["recipient"],
            subject=draft["subject"],
            body=draft["body"],
        )
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            # A 4xx means Gmail rejected the request, so nothing was sent.
            code = exc.response.status_code
            status, details = ("send_failed" if code < 500 else "delivery_uncertain"), {"http_status": code}
            message = f"Gmail send for draft {draft_id} ended with status={status}; staff must reconcile."
        else:
            status, details = "delivery_uncertain", {"error": type(exc).__name__}
            message = f"Gmail send for draft {draft_id} has an unknown outcome; do not retry, staff must reconcile."
        database.update_draft_status(draft_id, company_id=company_id, status=status)
        database.log_audit_event(
            company_id=company_id,
            actor_id=AGENT_ACTOR_ID,
            action=f"email_{status}",
            target_type="email_draft",
            target_id=draft_id,
            details=details,
        )
        raise ValueError(message) from exc

    database.update_draft_status(draft_id, company_id=company_id, status="sent")

    details: dict[str, Any] = {
        "gmail_message_id": gmail_message.get("id"),
        "gmail_thread_id": gmail_message.get("threadId"),
        "reviewed_by": draft.get("reviewed_by"),
    }
    try:
        details["hubspot_note_id"] = record_hubspot_email_note(draft["recipient"], draft["subject"], draft["body"])
    except Exception as exc:
        # The email is already sent; a CRM sync failure must not change delivery status.
        details["hubspot_sync_error"] = type(exc).__name__
    database.log_audit_event(
        company_id=company_id,
        actor_id=AGENT_ACTOR_ID,
        action="email_sent",
        target_type="email_draft",
        target_id=draft_id,
        details=details,
    )
    return f"gmail_message_id={gmail_message.get('id')}"


# =============================================================================
# SUPERVISOR TOOLS
# =============================================================================

@tool
def send_customer_email(
    draft_id: str,
    recipient: str,
    subject: str,
    body: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Send a saved inquiry reply draft through Gmail after policy, approval, and source checks."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")

    draft = database.get_email_draft(draft_id.strip(), company_id)
    validate_draft_for_delivery(
        draft,
        draft_id=draft_id,
        job_type="inquiry",
        recipient=recipient,
        subject=subject,
        body=body,
        context=runtime.context,
    )
    # Fetch the token before claiming, so an auth failure leaves the draft sendable.
    access_token = get_gmail_access_token()
    if not claim_draft_for_sending(draft, company_id):
        raise ValueError(f"Draft {draft_id} was already claimed by another delivery attempt.")

    result = deliver_claimed_draft(draft, company_id, access_token)
    return f"Sent inquiry email [draft_id={draft['id']}, status=sent, {result}]"


@tool
def send_retention_email(
    draft_id: str,
    recipient: str,
    subject: str,
    body: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Send a staff-approved retention draft through Gmail after rechecking couple eligibility."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")

    draft = database.get_email_draft(draft_id.strip(), company_id)
    validate_draft_for_delivery(
        draft,
        draft_id=draft_id,
        job_type="retention",
        recipient=recipient,
        subject=subject,
        body=body,
        context=runtime.context,
    )

    couple = get_couple_record(draft.get("contact_id") or "", company_id)
    if not couple:
        raise ValueError(f"No verified couple record for draft {draft_id}; cannot send retention outreach.")
    eligible, blockers = check_couple_eligibility(couple, company_id=company_id, exclude_draft_id=draft["id"])
    if not eligible:
        raise ValueError(f"Couple is no longer eligible for outreach: {', '.join(blockers)}.")

    access_token = get_gmail_access_token()
    if not claim_draft_for_sending(draft, company_id):
        raise ValueError(f"Draft {draft_id} was already claimed by another delivery attempt.")

    result = deliver_claimed_draft(draft, company_id, access_token)
    # Cooldown starts only after confirmed delivery.
    database.update_records(
        "couples",
        {"last_outreach_at": datetime.now(UTC).isoformat()},
        company_id=f"eq.{company_id}",
        contact_id=f"eq.{couple['contact_id']}",
    )
    return f"Sent retention email [draft_id={draft['id']}, contact_id={couple['contact_id']}, status=sent, {result}]"


@tool
def hand_off_to_staff(
    reason: str,
    runtime: ToolRuntime[AgentContext],
    draft_id: str = "",
) -> str:
    """Hand a conversation or draft to staff without sending anything, recording the reason."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")
    if not reason.strip():
        raise ValueError("A handoff reason is required.")

    target_type, target_id = "conversation", runtime.context.conversation_id or "unknown"
    if draft_id.strip():
        draft = database.get_email_draft(draft_id.strip(), company_id)
        if not draft:
            raise ValueError(f"No draft found for draft_id={draft_id}.")
        if draft["status"] in {"sending", "sent", "delivery_uncertain"}:
            raise ValueError(f"Draft {draft_id} is already in delivery (status={draft['status']}); cannot hand off.")
        database.update_draft_status(draft["id"], company_id=company_id, status="handed_off")
        target_type, target_id = "email_draft", draft["id"]

    database.log_audit_event(
        company_id=company_id,
        actor_id=AGENT_ACTOR_ID,
        action="handed_off_to_staff",
        target_type=target_type,
        target_id=target_id,
        details={"reason": reason.strip()},
    )
    return f"Handed off to staff [{target_type}={target_id}, status=handed_off]"

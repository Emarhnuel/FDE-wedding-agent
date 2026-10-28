import json
import os
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from langchain.tools import ToolRuntime, tool

from .. import database
from ..middleware import requires_human_review
from ..schemas import AgentContext

load_dotenv()

HUBSPOT_BASE_URL = "https://api.hubapi.com"
HUBSPOT_CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "phone",
    "lifecyclestage",
    "hs_lead_status",
]
# HubSpot-defined association type that links an email activity to a contact.
EMAIL_TO_CONTACT_ASSOCIATION = 198
# Website form fields shown under the customer's message in HubSpot.
INQUIRY_DETAILS = (("Preferred venue", "venue"), ("Guest count", "guest_count"), ("Preferred date", "preferred_date"))


def _hubspot_headers() -> dict[str, str]:
    token = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN", "")
    if not token:
        raise RuntimeError("HUBSPOT_PRIVATE_APP_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def find_hubspot_contact_by_email(email: str) -> dict[str, Any] | None:
    """Search HubSpot CRM for a contact by exact email address."""
    response = database.http.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/search",
        headers=_hubspot_headers(),
        json={
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email.strip(),
                        }
                    ]
                }
            ],
            "properties": HUBSPOT_CONTACT_PROPERTIES,
            "limit": 1,
        },
        timeout=15.0,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None


def get_hubspot_contact(contact_or_email: str) -> dict[str, Any] | None:
    """Retrieve a HubSpot contact by email address or numeric HubSpot contact ID."""
    value = contact_or_email.strip()
    if "@" in value:
        return find_hubspot_contact_by_email(value)
    response = database.http.get(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{value}",
        headers=_hubspot_headers(),
        params={"properties": ",".join(HUBSPOT_CONTACT_PROPERTIES)},
        timeout=15.0,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def upsert_hubspot_contact(
    email: str,
    *,
    couple_names: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Create or update a HubSpot contact for an incoming wedding inquiry."""
    existing = find_hubspot_contact_by_email(email)
    properties: dict[str, str] = {"email": email.strip()}
    if couple_names.strip():
        parts = couple_names.strip().split(None, 1)
        properties["firstname"] = parts[0]
        if len(parts) > 1:
            properties["lastname"] = parts[1]
    if phone.strip():
        properties["phone"] = phone.strip()

    if existing:
        contact_id = existing["id"]
        response = database.http.patch(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}",
            headers=_hubspot_headers(),
            json={"properties": properties},
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()

    properties.setdefault("lifecyclestage", "lead")
    response = database.http.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts",
        headers=_hubspot_headers(),
        json={"properties": properties},
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def log_hubspot_email(
    contact_id: str,
    *,
    incoming: bool,
    sender: str,
    recipient: str,
    subject: str,
    text: str,
    html: str = "",
    timestamp: str | None = None,
) -> str:
    """Log an email on the contact's HubSpot activity timeline; returns the email activity ID.

    incoming=True is the customer's message to us; False is a reply we sent. The activity only records
    the email in the CRM: nothing is sent through HubSpot.
    """
    properties = {
        "hs_timestamp": timestamp or datetime.now(UTC).isoformat(),
        "hs_email_direction": "INCOMING_EMAIL" if incoming else "EMAIL",
        "hs_email_status": "SENT",
        "hs_email_subject": subject,
        "hs_email_text": text,
        "hs_email_headers": json.dumps(
            {"from": {"email": sender}, "to": [{"email": recipient}], "cc": [], "bcc": []}
        ),
    }
    if html:
        properties["hs_email_html"] = html
    response = database.http.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/emails",
        headers=_hubspot_headers(),
        json={
            "properties": properties,
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": EMAIL_TO_CONTACT_ASSOCIATION}],
                }
            ],
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def record_incoming_inquiry(payload: dict[str, Any], received_at: str) -> str:
    """Create or update the customer's HubSpot contact and log their message as an incoming email.

    payload is an inquiry job's payload (website form or Gmail); returns the email activity ID.
    """
    email = payload["email"].strip()
    # Gmail falls back to the address when the sender has no display name; don't store it as a name.
    name = payload.get("name", "") if payload.get("name", "").strip().lower() != email.lower() else ""
    contact = upsert_hubspot_contact(email, couple_names=name, phone=payload.get("phone") or "")
    details = [f"{label}: {payload[key]}" for label, key in INQUIRY_DETAILS if payload.get(key)]
    text = payload["message"] + ("\n\n" + "\n".join(details) if details else "")
    return log_hubspot_email(
        str(contact["id"]),
        incoming=True,
        sender=email,
        recipient=os.environ.get("GMAIL_SENDER", ""),
        subject=payload.get("subject") or "Website inquiry",
        text=text,
        timestamp=received_at,
    )


@tool
def get_inquiry_history(
    contact_or_email: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Read scoped HubSpot CRM contact details and prior email drafts for an inquiry."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")

    contact = get_hubspot_contact(contact_or_email)
    lines: list[str] = []
    if contact:
        props = contact.get("properties", {})
        name = f"{props.get('firstname') or ''} {props.get('lastname') or ''}".strip() or "Unknown"
        lines.append(
            f"HubSpot Contact [id={contact['id']}]: name={name}, "
            f"email={props.get('email') or ''}, phone={props.get('phone') or 'none'}, "
            f"stage={props.get('lifecyclestage') or 'unknown'}"
        )
        lookup_email = props.get("email") or contact_or_email
    else:
        lines.append(f"No existing HubSpot contact found for {contact_or_email}.")
        lookup_email = contact_or_email

    if "@" in lookup_email:
        drafts = database.query_records(
            "email_drafts",
            company_id=f"eq.{company_id}",
            recipient=f"eq.{lookup_email.strip()}",
            order="created_at.desc",
            limit=5,
        )
    else:
        drafts = database.query_records(
            "email_drafts",
            company_id=f"eq.{company_id}",
            contact_id=f"eq.{lookup_email.strip()}",
            order="created_at.desc",
            limit=5,
        )

    if drafts:
        lines.append("Prior drafts:")
        for d in drafts:
            lines.append(
                f"- [draft_id={d['id']}, status={d['status']}] Subject: {d['subject']}"
            )
    else:
        lines.append("No prior email drafts on record.")

    return "\n".join(lines)


@tool
def save_inquiry_draft(
    recipient: str,
    subject: str,
    body: str,
    runtime: ToolRuntime[AgentContext],
    couple_names: str = "",
    phone: str = "",
    requires_review: bool = True,
    review_reasons: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Upsert the inquiry contact in HubSpot and save the email draft in InsForge."""
    company_id = runtime.context.company_id
    if not company_id:
        raise ValueError("Missing server-verified company_id in runtime context.")
    # Rejected rather than rewritten here: the send tool requires the exact saved text, and routine
    # replies may send without a human reading them first.
    if "—" in subject + body or "–" in subject + body:
        raise ValueError(
            "Rewrite the subject and body without em dashes or en dashes: use a comma, a period, "
            "or 'to' for ranges (May to October). Then call save_inquiry_draft again."
        )

    contact = upsert_hubspot_contact(recipient, couple_names=couple_names, phone=phone)
    contact_id = str(contact["id"])

    needs_review = (
        requires_review
        or runtime.context.requires_approval
        or requires_human_review(f"{subject}\n{body}", job_type="inquiry")
    )
    reasons = list(review_reasons or [])
    if needs_review and not reasons:
        reasons.append("restricted_topic_or_policy_check")

    thread_id = runtime.context.conversation_id or f"inquiry:{contact_id}"
    draft = database.save_email_draft(
        company_id=company_id,
        thread_id=thread_id,
        contact_id=contact_id,
        recipient=recipient.strip(),
        subject=subject.strip(),
        body=body.strip(),
        job_type="inquiry",
        sources=sources or [],
        requires_review=needs_review,
        review_reasons=reasons,
        status="pending_review" if needs_review else "drafted",
    )
    return (
        f"Saved inquiry draft [draft_id={draft['id']}, contact_id={contact_id}, "
        f"status={draft['status']}, requires_review={needs_review}]"
    )

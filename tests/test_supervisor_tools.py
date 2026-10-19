"""End-to-end tests: real inquiries run through the supervisor agent and are delivered via Gmail.

Each test drives the full workflow: knowledge retrieval, drafting (paused for staff review),
guarded delivery (paused when policy requires it), staff approval in the "portal", and a
real Gmail send. Uses live OpenRouter, ChromaDB, InsForge, HubSpot, and Gmail, so it costs
model tokens and sends real emails to TEST_EMAIL_RECIPIENT (defaults to GMAIL_SENDER).
"""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from langgraph.types import Command

from src import database, rag
from src.schemas import AgentContext
from src.tools.inquiry_tools import HUBSPOT_BASE_URL, _hubspot_headers, find_hubspot_contact_by_email

PDF_PATH = Path(__file__).resolve().parent.parent / "juniper-hollow-knowledge-pack.pdf"
COMPANY_ID = f"test-supervisor-{uuid4().hex[:8]}"
SOURCE_ID, SOURCE_VERSION = "JH-KB-2026-03", "3.0"
RECIPIENT = os.environ.get("TEST_EMAIL_RECIPIENT") or os.environ.get("GMAIL_SENDER", "")
STAFF_ID = "staff-test-approver"
MAX_REVIEW_ROUNDS = 8


@pytest.fixture(scope="module")
def supervisor():
    assert RECIPIENT, "Set TEST_EMAIL_RECIPIENT or GMAIL_SENDER in .env"
    database.init_schema()

    # Publish the knowledge pack for this test company only.
    rag.index_document(
        PDF_PATH,
        company_id=COMPANY_ID,
        source_id=SOURCE_ID,
        version=SOURCE_VERSION,
        title="Juniper Hollow Company Knowledge Pack",
    )
    database.insert_record(
        "documents",
        {"company_id": COMPANY_ID, "source_id": SOURCE_ID, "version": SOURCE_VERSION, "status": "published"},
    )

    # Only delete the HubSpot contact afterwards if the tests created it.
    preexisting_contact = find_hubspot_contact_by_email(RECIPIENT)

    from src.agents import supervisor as agent

    yield agent

    for table in ("email_drafts", "couples", "documents", "audit_logs", "knowledge_chunks"):
        database.run_raw_sql(f"DELETE FROM {table} WHERE company_id LIKE 'test-supervisor-%';")
    if not preexisting_contact and (contact := find_hubspot_contact_by_email(RECIPIENT)):
        httpx.delete(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact['id']}",
            headers=_hubspot_headers(),
            timeout=15.0,
        )


def staff_portal_approve(draft_id: str) -> None:
    """Simulate an email_approver clicking Approve & Send in the staff portal."""
    database.update_draft_status(draft_id, company_id=COMPANY_ID, status="approved", reviewed_by=STAFF_ID)
    database.log_audit_event(
        company_id=COMPANY_ID,
        actor_id=STAFF_ID,
        action="draft_approved",
        target_type="email_draft",
        target_id=draft_id,
    )


def run_with_staff_review(agent, message: str, context: AgentContext, decide) -> list[tuple[str, dict]]:
    """Invoke the supervisor and answer every HITL pause with decide(name, args) until the run ends.

    decide returns (decision, context) so a staff approval can update the server-verified context
    for the resume, the way the API would after recording the decision.
    """
    config = {"configurable": {"thread_id": context.conversation_id}, "recursion_limit": 150}
    result = agent.invoke(
        {"messages": [{"role": "user", "content": message}]}, config=config, context=context, version="v2"
    )
    reviewed: list[tuple[str, dict]] = []
    for _ in range(MAX_REVIEW_ROUNDS):
        if not result.interrupts:
            return reviewed
        resume = {}
        for interrupt in result.interrupts:
            decisions = []
            for request in interrupt.value["action_requests"]:
                args = request.get("args") or request.get("arguments") or {}
                reviewed.append((request["name"], args))
                decision, context = decide(request["name"], args, context)
                decisions.append(decision)
            resume[interrupt.id] = {"decisions": decisions}
        payload = next(iter(resume.values())) if len(resume) == 1 else resume
        result = agent.invoke(Command(resume=payload), config=config, context=context, version="v2")
    pytest.fail(f"Run still paused after {MAX_REVIEW_ROUNDS} review rounds: {[name for name, _ in reviewed]}")


def approve_everything(name: str, args: dict, context: AgentContext):
    """Staff approve drafts as written; sends are approved in the portal first."""
    if name.startswith("send_"):
        staff_portal_approve(args["draft_id"])
        context = replace(context, staff_approved=True)
    return {"type": "approve"}, context


def inquiry_email(subject: str, body: str) -> str:
    return (
        "New customer email received in the shared Gmail inbox.\n"
        f"From: Jordan Avery <{RECIPIENT}>\n"
        f"Subject: {subject}\n\n{body}"
    )


def drafts_for(conversation_id: str) -> list[dict]:
    return database.query_records(
        "email_drafts", company_id=f"eq.{COMPANY_ID}", thread_id=f"eq.{conversation_id}", order="created_at.desc"
    )


def audit_actions(draft_id: str) -> set[str]:
    rows = database.query_records("audit_logs", company_id=f"eq.{COMPANY_ID}", target_id=f"eq.{draft_id}")
    return {row["action"] for row in rows}


def test_routine_inquiry_is_drafted_reviewed_and_sent(supervisor):
    conversation_id = f"test-routine-{uuid4().hex[:8]}"
    context = AgentContext(company_id=COMPANY_ID, conversation_id=conversation_id, job_type="inquiry")

    reviewed = run_with_staff_review(
        supervisor,
        inquiry_email(
            "Wi-Fi and guest capacity",
            "Hi! Does Hollow Brook Barn have Wi-Fi for guests, and how many guests can it hold "
            "for a seated dinner? Thanks, Jordan",
        ),
        context,
        approve_everything,
    )

    assert "save_inquiry_draft" in [name for name, _ in reviewed], "Drafting must pause for staff review"
    sent = [d for d in drafts_for(conversation_id) if d["status"] == "sent"]
    assert len(sent) == 1, f"Expected exactly one sent draft, got {[d['status'] for d in drafts_for(conversation_id)]}"
    assert sent[0]["recipient"] == RECIPIENT
    assert sent[0]["sent_at"]
    assert "email_sent" in audit_actions(sent[0]["id"])


def test_pricing_inquiry_waits_for_staff_approval_before_sending(supervisor):
    conversation_id = f"test-pricing-{uuid4().hex[:8]}"
    context = AgentContext(company_id=COMPANY_ID, conversation_id=conversation_id, job_type="inquiry")
    states_at_send_review: list[dict] = []

    def decide(name: str, args: dict, ctx: AgentContext):
        if name == "send_customer_email":
            # Before staff act, the stored draft must be held for review and not yet sent.
            states_at_send_review.append(database.get_email_draft(args["draft_id"], COMPANY_ID))
        return approve_everything(name, args, ctx)

    run_with_staff_review(
        supervisor,
        inquiry_email(
            "Saturday pricing for October 2027",
            "What is the rental price for Hollow Brook Barn on a Saturday in October 2027, "
            "and can you hold October 16 for us? We expect about 150 guests.",
        ),
        context,
        decide,
    )

    assert states_at_send_review, "A pricing reply must pause at the send step for staff approval"
    held = states_at_send_review[0]
    assert held["requires_review"] is True
    assert held["status"] == "pending_review"

    sent = [d for d in drafts_for(conversation_id) if d["status"] == "sent"]
    assert len(sent) == 1
    assert sent[0]["reviewed_by"] == STAFF_ID
    assert "email_sent" in audit_actions(sent[0]["id"])


def test_rejected_reply_is_never_sent(supervisor):
    conversation_id = f"test-reject-{uuid4().hex[:8]}"
    context = AgentContext(company_id=COMPANY_ID, conversation_id=conversation_id, job_type="inquiry")

    def decide(name: str, args: dict, ctx: AgentContext):
        if name.startswith("send_"):
            database.update_draft_status(args["draft_id"], company_id=COMPANY_ID, status="rejected", reviewed_by=STAFF_ID)
            return {"type": "reject", "message": "Staff rejected this reply; do not send it."}, ctx
        return {"type": "approve"}, ctx

    run_with_staff_review(
        supervisor,
        inquiry_email(
            "Discount request",
            "We're a small wedding of 40 people. Can you give us a 30% discount on the Glasshouse?",
        ),
        context,
        decide,
    )

    drafts = drafts_for(conversation_id)
    assert drafts, "The agent should still save a draft for staff"
    assert all(d["status"] != "sent" for d in drafts)
    assert all("email_sent" not in audit_actions(d["id"]) for d in drafts)


def test_retention_outreach_is_approved_sent_and_starts_cooldown(supervisor):
    conversation_id = f"test-retention-{uuid4().hex[:8]}"
    contact_id = f"couple-{uuid4().hex[:8]}"
    database.insert_record(
        "couples",
        {
            "company_id": COMPANY_ID,
            "contact_id": contact_id,
            "couple_names": "Ava & Elena",
            "email": RECIPIENT,
            "venue": "Hollow Brook Barn",
            "wedding_date": (datetime.now(UTC) - timedelta(days=365)).date().isoformat(),
            "marketing_consent": True,
            "do_not_contact": False,
            "has_open_complaint": False,
            "last_outreach_at": (datetime.now(UTC) - timedelta(days=120)).isoformat(),
        },
    )
    context = AgentContext(
        company_id=COMPANY_ID,
        contact_id=contact_id,
        conversation_id=conversation_id,
        job_type="retention",
        marketing_consent=True,
    )
    started = datetime.now(UTC)

    reviewed = run_with_staff_review(
        supervisor,
        f"Daily retention job: couple contact_id={contact_id} is eligible for first-anniversary "
        f"outreach (occasion=anniversary). Draft the message and send it once staff approve.",
        context,
        approve_everything,
    )

    names = [name for name, _ in reviewed]
    assert "save_retention_draft" in names, "Retention drafting must pause for staff review"
    assert "send_retention_email" in names, "Retention sends must always pause for staff approval"

    sent = [d for d in drafts_for(conversation_id) if d["status"] == "sent"]
    assert len(sent) == 1
    assert sent[0]["job_type"] == "retention"

    couple = database.query_records("couples", company_id=f"eq.{COMPANY_ID}", contact_id=f"eq.{contact_id}")[0]
    last_outreach = datetime.fromisoformat(couple["last_outreach_at"].replace("Z", "+00:00"))
    assert last_outreach >= started - timedelta(seconds=5), "Cooldown must start after confirmed delivery"

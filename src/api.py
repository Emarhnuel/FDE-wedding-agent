"""FastAPI web API: public inquiry intake and authenticated staff review of paused agent actions.

The API never runs the agent. It validates, records decisions, and queues jobs; the AgentCore
runtime does the long work. When the Deep Agents supervisor pauses at an `interrupt_on` tool
(save_*_draft or send_*), the runtime stores one `agent_reviews` row per paused action. Staff
decide here, and once every action of that interrupt is decided a `resume` job is queued so the
runtime can resume the same thread with the stored decisions.
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import boto3
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel, Field

from . import database
from .security.auth import AuthError, StaffIdentity, authenticate_staff
from .security.permissions import Action, authorize

# Trusted server configuration; never taken from request payloads.
COMPANY_ID = os.environ.get("COMPANY_ID", "juniper-hollow")
JOBS_QUEUE_URL = os.environ.get("JOBS_QUEUE_URL", "")
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o]

app = FastAPI(title="Juniper Hollow Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(AuthError)
def auth_error_handler(_request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def current_staff(authorization: str | None = Header(default=None)) -> StaffIdentity:
    return authenticate_staff(authorization)


def queue_job(kind: str, thread_id: str, dedupe_key: str, payload: dict) -> dict:
    """Record the job durably, then hand it to SQS once; duplicates are not re-sent."""
    job, created = database.enqueue_job(
        company_id=COMPANY_ID, kind=kind, thread_id=thread_id, dedupe_key=dedupe_key, payload=payload
    )
    if created and JOBS_QUEUE_URL:
        # One message group per thread, so a conversation is never processed concurrently.
        boto3.client("sqs").send_message(
            QueueUrl=JOBS_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job["id"]}),
            MessageGroupId=thread_id,
            MessageDeduplicationId=job["id"],
        )
    return job


# =============================================================================
# PUBLIC
# =============================================================================

class InquiryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    phone: str = Field(default="", max_length=50)
    message: str = Field(min_length=1, max_length=5000)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/inquiries", status_code=202)
def submit_inquiry(inquiry: InquiryIn) -> dict:
    """Accept a website inquiry and queue it; returns before any agent work runs."""
    fingerprint = hashlib.sha256(f"{COMPANY_ID}|{inquiry.email.lower()}|{inquiry.message}".encode()).hexdigest()
    job = queue_job(
        "inquiry",
        thread_id=f"inquiry:{uuid4()}",
        dedupe_key=f"inquiry:{fingerprint}",
        payload={"source": "web_form", **inquiry.model_dump()},
    )
    return {"job_id": job["id"], "status": job["status"]}


# =============================================================================
# STAFF REVIEW
# =============================================================================

class DecisionIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    message: str | None = Field(default=None, max_length=1000)


ACTION_FOR_DECISION = {"approve": Action.APPROVE_DRAFT, "edit": Action.EDIT_DRAFT, "reject": Action.REJECT_DRAFT}


def load_review(review_id: str, staff: StaffIdentity, action: Action) -> dict:
    rows = database.query_records("agent_reviews", id=f"eq.{review_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Review not found.")
    # Company scope comes from the stored record, never the request.
    authorize(staff, action, company_id=rows[0]["company_id"])
    return rows[0]


@app.get("/reviews")
def list_pending_reviews(staff: StaffIdentity = Depends(current_staff)) -> list[dict]:
    authorize(staff, Action.VIEW_DRAFTS, company_id=staff.company_id)
    return database.query_records(
        "agent_reviews", company_id=f"eq.{staff.company_id}", status="eq.pending", order="created_at.asc"
    )


@app.get("/reviews/{review_id}")
def get_review(review_id: str, staff: StaffIdentity = Depends(current_staff)) -> dict:
    review = load_review(review_id, staff, Action.VIEW_DRAFTS)
    draft = database.get_email_draft(review["draft_id"], review["company_id"]) if review["draft_id"] else None
    return {**review, "draft": draft}


@app.post("/reviews/{review_id}/decision")
def decide_review(review_id: str, decision_in: DecisionIn, staff: StaffIdentity = Depends(current_staff)) -> dict:
    """Approve, edit (submit and approve revised content), or reject one paused agent action."""
    review = load_review(review_id, staff, ACTION_FOR_DECISION[decision_in.decision])
    company_id = review["company_id"]

    if decision_in.decision == "approve":
        decision = {"type": "approve"}
    elif decision_in.decision == "reject":
        decision = {"type": "reject", "message": decision_in.message or "Staff rejected this action. Do not retry it."}
    else:
        edits = {k: v for k, v in {"subject": decision_in.subject, "body": decision_in.body}.items() if v}
        if not edits:
            raise HTTPException(422, "An edit must change the subject or body.")
        decision = {"type": "edit", "edited_action": {"name": review["action_name"], "args": {**review["action_args"], **edits}}}

    now = datetime.now(UTC).isoformat()
    # Conditional update is the lock: only one staff decision can claim a pending review.
    claimed = database.update_records(
        "agent_reviews",
        {"status": "decided", "decision": decision, "decided_by": staff.staff_id, "decided_at": now},
        id=f"eq.{review_id}",
        status="eq.pending",
    )
    if not claimed:
        raise HTTPException(409, "This review was already decided.")

    if review["draft_id"]:
        # Send step: record the approval the guarded send tools verify (or block the draft).
        draft_update = {"status": "rejected" if decision["type"] == "reject" else "approved",
                        "reviewed_by": staff.staff_id, "reviewed_at": now}
        if decision["type"] == "edit":
            draft_update.update(edits)
        if not database.update_records(
            "email_drafts", draft_update, id=f"eq.{review['draft_id']}",
            company_id=f"eq.{company_id}", status="in.(pending_review,drafted)",
        ):
            database.update_records(
                "agent_reviews", {"status": "pending", "decision": None, "decided_by": None, "decided_at": None},
                id=f"eq.{review_id}",
            )
            raise HTTPException(409, "The draft is no longer awaiting review.")

    database.log_audit_event(
        company_id=company_id,
        actor_id=staff.staff_id,
        action=f"review_{decision['type']}",
        target_type="agent_review",
        target_id=review_id,
        details={"action_name": review["action_name"], "draft_id": review["draft_id"]},
    )

    # Resume only after every paused action in this interrupt has a decision.
    pending = database.query_records(
        "agent_reviews", interrupt_id=f"eq.{review['interrupt_id']}", status="eq.pending", limit=1
    )
    job = None
    if not pending:
        job = queue_job(
            "resume",
            thread_id=review["thread_id"],
            dedupe_key=f"resume:{review['interrupt_id']}",
            payload={"interrupt_id": review["interrupt_id"]},
        )
    return {"review_id": review_id, "decision": decision["type"], "resume_job_id": job["id"] if job else None}


handler = Mangum(app)

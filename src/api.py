"""FastAPI web API: public inquiry intake, staff review of paused agent actions, staff administration,
and knowledge document management.

The API never runs the agent. It validates, records decisions, and queues jobs; the AgentCore
runtime does the long work (locally, RUN_JOBS_LOCALLY=true runs queued jobs in-process instead). When the Deep Agents supervisor pauses at an `interrupt_on` tool
(save_*_draft or send_*), the runtime stores one `agent_reviews` row per paused action. Staff
decide here, and once every action of that interrupt is decided a `resume` job is queued so the
runtime can resume the same thread with the stored decisions.
"""

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import boto3
from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from mangum import Mangum
from pydantic import BaseModel, Field
from pypdf.errors import PdfReadError

from . import database
from .security.auth import AuthError, StaffIdentity, authenticate_staff, create_login, send_password_setup, sign_in
from .security.permissions import STAFF_ADMIN, Action, authorize

# Trusted server configuration; never taken from request payloads.
COMPANY_ID = os.environ.get("COMPANY_ID", "juniper-hollow")
JOBS_QUEUE_URL = os.environ.get("JOBS_QUEUE_URL", "")
# Local development only: with no SQS queue or AgentCore, run each queued job in this process.
RUN_JOBS_LOCALLY = os.environ.get("RUN_JOBS_LOCALLY", "").lower() == "true"
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o]

app = FastAPI(title="Juniper Hollow Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(AuthError)
def auth_error_handler(_request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


# Adds Swagger's Authorize button, which signs in through POST /auth/login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def current_staff(token: str | None = Depends(oauth2_scheme)) -> StaffIdentity:
    return authenticate_staff(f"Bearer {token}" if token else None)


def send_job(job: dict, dedupe_id: str | None = None) -> None:
    """Hand a recorded job to the FIFO queue, or run it in-process when RUN_JOBS_LOCALLY is set."""
    if JOBS_QUEUE_URL:
        # One message group per thread, so a conversation is never processed concurrently.
        boto3.client("sqs").send_message(
            QueueUrl=JOBS_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job["id"]}),
            MessageGroupId=job["thread_id"],
            MessageDeduplicationId=dedupe_id or job["id"],
        )
    elif RUN_JOBS_LOCALLY:
        from .runtime import run_job  # Imported here so the deployed API never loads the agent.

        threading.Thread(target=run_job, args=(job["id"],), daemon=True).start()


def queue_job(kind: str, thread_id: str, dedupe_key: str, payload: dict) -> dict:
    """Record the job durably, then hand it to SQS once; duplicates are not re-sent."""
    job, created = database.enqueue_job(
        company_id=COMPANY_ID, kind=kind, thread_id=thread_id, dedupe_key=dedupe_key, payload=payload
    )
    if created:
        send_job(job)
    return job


# =============================================================================
# PUBLIC
# =============================================================================

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class InquiryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    phone: str = Field(default="", max_length=50)
    message: str = Field(min_length=1, max_length=5000)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()) -> dict:
    """Sign in with staff email (as username) and password; returns an InsForge access token."""
    return {"access_token": sign_in(form.username, form.password), "token_type": "bearer"}


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


def original_request(review: dict) -> dict | None:
    """The customer inquiry (or retention selection) that started the review's thread, for reviewers."""
    rows = database.query_records(
        "jobs", company_id=f"eq.{review['company_id']}", thread_id=f"eq.{review['thread_id']}",
        kind="neq.resume", order="created_at.asc", limit=1,
    )
    return rows[0]["payload"] if rows else None


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
    reviews = database.query_records(
        "agent_reviews", company_id=f"eq.{staff.company_id}", status="eq.pending", order="created_at.asc"
    )
    return [{**review, "original_request": original_request(review)} for review in reviews]


@app.get("/reviews/{review_id}")
def get_review(review_id: str, staff: StaffIdentity = Depends(current_staff)) -> dict:
    review = load_review(review_id, staff, Action.VIEW_DRAFTS)
    draft = database.get_email_draft(review["draft_id"], review["company_id"]) if review["draft_id"] else None
    return {**review, "original_request": original_request(review), "draft": draft}


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


# =============================================================================
# STAFF ADMINISTRATION
# =============================================================================

StaffRole = Literal["email_approver", "knowledge_manager", "staff_admin"]


class StaffIn(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)
    full_name: str = Field(min_length=1, max_length=200)
    roles: list[StaffRole] = Field(min_length=1)


class StaffUpdate(BaseModel):
    is_active: bool | None = None
    roles: list[StaffRole] | None = Field(default=None, min_length=1)


@app.get("/staff")
def list_staff(staff: StaffIdentity = Depends(current_staff)) -> list[dict]:
    authorize(staff, Action.VIEW_STAFF, company_id=staff.company_id)
    return database.query_records("staff_members", company_id=f"eq.{staff.company_id}", order="full_name.asc")


@app.post("/staff", status_code=201)
def add_staff(staff_in: StaffIn, staff: StaffIdentity = Depends(current_staff)) -> dict:
    """Create a staff login and membership; InsForge emails the new member a code to set their password."""
    authorize(staff, Action.MANAGE_STAFF, company_id=staff.company_id)
    email = staff_in.email.lower()
    member = database.insert_record(
        "staff_members",
        {"insforge_user_id": create_login(email, staff_in.full_name), "company_id": staff.company_id,
         "email": email, "full_name": staff_in.full_name, "roles": sorted(set(staff_in.roles))},
    )
    database.log_audit_event(
        company_id=staff.company_id, actor_id=staff.staff_id, action="staff_added",
        target_type="staff_member", target_id=member["id"], details={"roles": member["roles"]},
    )
    return {**member, "invite_sent": send_password_setup(email)}


@app.patch("/staff/{staff_id}")
def update_staff(staff_id: str, update: StaffUpdate, staff: StaffIdentity = Depends(current_staff)) -> dict:
    """Deactivate, reactivate, or change roles; takes effect on the member's next request."""
    rows = database.query_records("staff_members", id=f"eq.{staff_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Staff member not found.")
    authorize(staff, Action.MANAGE_STAFF, company_id=rows[0]["company_id"])
    changes = update.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to update.")
    if "roles" in changes:
        changes["roles"] = sorted(set(changes["roles"]))
    # An admin cannot lock themselves out, so the company always keeps an active staff_admin.
    if staff_id == staff.staff_id and (changes.get("is_active") is False or STAFF_ADMIN not in changes.get("roles", [STAFF_ADMIN])):
        raise HTTPException(409, "You cannot deactivate yourself or remove your own staff_admin role.")
    member = database.update_records("staff_members", changes, id=f"eq.{staff_id}")[0]
    database.log_audit_event(
        company_id=member["company_id"], actor_id=staff.staff_id, action="staff_updated",
        target_type="staff_member", target_id=staff_id, details=changes,
    )
    return member


# =============================================================================
# KNOWLEDGE DOCUMENTS
# =============================================================================

DOCUMENT_TYPES = {".pdf", ".txt", ".md"}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# Source IDs and versions end up in file names and chunk IDs, so only safe characters are allowed.
ID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"


@app.get("/documents")
def list_documents(staff: StaffIdentity = Depends(current_staff)) -> list[dict]:
    authorize(staff, Action.VIEW_DOCUMENTS, company_id=staff.company_id)
    return database.query_records("documents", company_id=f"eq.{staff.company_id}", order="source_id.asc,created_at.desc")


@app.post("/documents", status_code=201)
def upload_document(
    file: UploadFile,
    source_id: str = Form(pattern=ID_PATTERN),
    version: str = Form(pattern=ID_PATTERN),
    title: str = Form(min_length=1, max_length=200),
    venue: str = Form(default="", max_length=200),
    staff: StaffIdentity = Depends(current_staff),
) -> dict:
    """Upload and index a knowledge file as a new version that is not live until published ("ready")."""
    from . import rag  # Loaded only here: indexing pulls in Chroma and the embedding client.

    existing = database.query_records("documents", company_id=f"eq.{staff.company_id}", source_id=f"eq.{source_id}")
    # A new version of a known document is a replacement.
    authorize(staff, Action.REPLACE_DOCUMENT if existing else Action.UPLOAD_DOCUMENT, company_id=staff.company_id)
    if any(document["version"] == version for document in existing):
        raise HTTPException(409, f"{source_id} version {version} already exists.")

    suffix = Path(file.filename or "").suffix.lower()
    content = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if suffix not in DOCUMENT_TYPES:
        raise HTTPException(422, "Only PDF, TXT, and Markdown files are supported.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    # Check the bytes themselves, not just the file name.
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(422, "The file is not a valid PDF.")
    if suffix != ".pdf":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Text and Markdown files must be UTF-8.") from exc

    path = rag.UPLOAD_DIRECTORY / staff.company_id / f"{source_id}_{version}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        # Indexed as "staged": invisible to the agent until a knowledge manager publishes it.
        chunks = rag.index_document(
            path, company_id=staff.company_id, source_id=source_id, version=version, title=title, venue=venue,
            status="staged",
        )
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, PdfReadError)):
            raise HTTPException(422, "No readable text could be extracted from this file.") from exc
        raise
    document = database.insert_record(
        "documents",
        {"company_id": staff.company_id, "source_id": source_id, "version": version, "title": title,
         "venue": venue, "status": "ready", "uploaded_by": staff.staff_id},
    )
    database.log_audit_event(
        company_id=staff.company_id, actor_id=staff.staff_id, action="upload_document",
        target_type="document", target_id=document["id"], details={"source_id": source_id, "version": version, "chunks": chunks},
    )
    return {**document, "chunks": chunks}


def activate_version(document_id: str, staff: StaffIdentity, action: Action, from_status: str) -> dict:
    """Make one version the live version of its document; the previous live version becomes superseded."""
    from . import rag

    rows = database.query_records("documents", id=f"eq.{document_id}", limit=1)
    if not rows:
        raise HTTPException(404, "Document not found.")
    document = rows[0]
    authorize(staff, action, company_id=document["company_id"])
    if document["status"] != from_status:
        raise HTTPException(409, f"This version is '{document['status']}'; only a '{from_status}' version qualifies.")

    company_id, source_id = document["company_id"], document["source_id"]
    live = database.query_records("documents", company_id=f"eq.{company_id}", source_id=f"eq.{source_id}", status="eq.published")
    # Search switches first. If the database update then fails, the database still names the old
    # version as published, so drafts citing the new one are blocked at send time, never mis-sent.
    rag.set_version_status(company_id, source_id, document["version"], "published")
    for old in live:
        rag.set_version_status(company_id, source_id, old["version"], "superseded")
        database.update_records("documents", {"status": "superseded"}, id=f"eq.{old['id']}")
    published = database.update_records(
        "documents", {"status": "published", "published_at": datetime.now(UTC).isoformat()}, id=f"eq.{document_id}"
    )[0]
    database.log_audit_event(
        company_id=company_id, actor_id=staff.staff_id, action=action.value, target_type="document",
        target_id=document_id, details={"source_id": source_id, "version": document["version"],
                                        "replaced_versions": [old["version"] for old in live]},
    )
    return published


@app.post("/documents/{document_id}/publish")
def publish_document(document_id: str, staff: StaffIdentity = Depends(current_staff)) -> dict:
    """Make an uploaded ("ready") version live for the agent and the delivery checks."""
    return activate_version(document_id, staff, Action.PUBLISH_DOCUMENT, "ready")


@app.post("/documents/{document_id}/rollback")
def rollback_document(document_id: str, staff: StaffIdentity = Depends(current_staff)) -> dict:
    """Make an earlier ("superseded") version live again."""
    return activate_version(document_id, staff, Action.ROLLBACK_DOCUMENT, "superseded")


handler = Mangum(app)

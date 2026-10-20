"""AgentCore Runtime entrypoint: runs queued jobs on the Deep Agents supervisor and records HITL pauses.

The worker invokes this runtime with {"job_id": ...}. The runtime acknowledges immediately and runs
the job in a tracked background task. When the supervisor pauses at an `interrupt_on` tool, each
paused action is stored in `agent_reviews` for staff (see api.py); a later `resume` job continues
the same thread with the stored decisions. Paused state lives in the Postgres checkpointer, so a
resume can arrive days later in a new runtime session.
"""

import os
import threading
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import database
from .agents import build_supervisor
from .schemas import AgentContext
from .tools.retention_tools import check_couple_eligibility, get_couple_record

DATABASE_URL = os.environ.get("DATABASE_URL", "")

app = BedrockAgentCoreApp()


_agent = None
_agent_lock = threading.Lock()


def get_agent():
    """Build the supervisor once per process with Postgres-backed checkpoints and memory store."""
    global _agent
    # Jobs run in parallel threads; the lock stops two of them building agents and pools at once.
    with _agent_lock:
        if _agent is None:
            if not DATABASE_URL:
                # Local development only: paused runs are lost when the process exits.
                _agent = build_supervisor()
            else:
                pool = ConnectionPool(
                    DATABASE_URL,
                    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                    # The database closes idle connections; test each one and reconnect if it died.
                    check=ConnectionPool.check_connection,
                    open=True,
                )
                checkpointer, store = PostgresSaver(pool), PostgresStore(pool)
                checkpointer.setup()
                store.setup()
                _agent = build_supervisor(checkpointer, store)
        return _agent


def set_job_status(job_id: str, status: str, **fields) -> None:
    database.update_records(
        "jobs", {"status": status, "updated_at": datetime.now(UTC).isoformat(), **fields}, id=f"eq.{job_id}"
    )


def build_start(job: dict, resuming: bool = False) -> tuple[AgentContext, str] | None:
    """Rebuild trusted context and the task message from database records (None = skip the job)."""
    payload = job["payload"]
    if job["kind"] == "inquiry":
        context = AgentContext(company_id=job["company_id"], conversation_id=job["thread_id"], job_type="inquiry")
        message = (
            f"New customer inquiry via {payload.get('source', 'web_form')}.\n"
            f"From: {payload['name']} <{payload['email']}>\nPhone: {payload.get('phone') or 'not provided'}\n"
        )
        # Optional website form fields; Gmail inquiries don't have them.
        for label, key in (("Preferred venue", "venue"), ("Guest count", "guest_count"), ("Preferred date", "preferred_date")):
            if payload.get(key):
                message += f"{label}: {payload[key]}\n"
        if payload.get("subject"):
            # Gmail only threads a reply whose subject matches the customer's email.
            subject = payload["subject"]
            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            message += f"Subject: {subject}\nUse exactly this subject for the reply draft: {reply_subject}\n"
        return context, f"{message}\n{payload['message']}"

    if job["kind"] == "retention":
        couple = get_couple_record(payload["contact_id"], job["company_id"])
        if not couple:
            return None
        _, blockers = check_couple_eligibility(couple, company_id=job["company_id"])
        if resuming:
            # This thread's own draft is the pending one; every other rule still applies.
            blockers = [b for b in blockers if b != "existing_pending_retention_draft"]
        if blockers:
            return None
        context = AgentContext(
            company_id=job["company_id"],
            contact_id=couple["contact_id"],
            conversation_id=job["thread_id"],
            job_type="retention",
            marketing_consent=True,
            cooldown_active=False,
        )
        message = (
            f"Daily retention job: couple contact_id={couple['contact_id']} is eligible for "
            f"{payload['occasion']} outreach. Draft the message for staff review."
        )
        return context, message

    raise ValueError(f"Unknown job kind: {job['kind']}")


def build_resume(job: dict) -> tuple[AgentContext, Command]:
    """Rebuild the original job's context and the staff decisions for one paused interrupt."""
    interrupt_id = job["payload"]["interrupt_id"]
    reviews = database.query_records("agent_reviews", interrupt_id=f"eq.{interrupt_id}", order="action_index.asc")
    if not reviews or any(r["status"] != "decided" for r in reviews):
        raise ValueError(f"Interrupt {interrupt_id} has undecided reviews.")

    origin = database.query_records(
        "jobs", thread_id=f"eq.{job['thread_id']}", kind="neq.resume", order="created_at.asc", limit=1
    )[0]
    # Context is not saved in checkpoints, so it is rebuilt from current records on every resume.
    start = build_start(origin, resuming=True)
    if start is None:
        raise ValueError("The original job is no longer eligible to continue.")
    context = replace(
        start[0],
        # Recorded staff approval of a send step; the send tools verify it again against the draft.
        staff_approved=any(
            r["action_name"].startswith("send_") and r["decision"]["type"] in ("approve", "edit") for r in reviews
        ),
    )
    return context, Command(resume={interrupt_id: {"decisions": [r["decision"] for r in reviews]}})


def record_interrupts(job: dict, interrupts) -> None:
    """Store one pending agent_reviews row per paused action; skip interrupts already recorded."""
    recorded = {r["interrupt_id"] for r in database.query_records("agent_reviews", thread_id=f"eq.{job['thread_id']}")}
    for interrupt in interrupts:
        if interrupt.id in recorded:
            continue
        action_requests = interrupt.value.get("action_requests") if isinstance(interrupt.value, dict) else None
        if not action_requests:
            raise ValueError(f"Interrupt {interrupt.id} is not a human-in-the-loop review request.")
        for index, request in enumerate(action_requests):
            database.insert_record(
                "agent_reviews",
                {
                    "company_id": job["company_id"],
                    "thread_id": job["thread_id"],
                    "interrupt_id": interrupt.id,
                    "action_index": index,
                    "action_name": request["name"],
                    "action_args": request["args"],
                    "draft_id": request["args"].get("draft_id") if request["name"].startswith("send_") else None,
                },
            )


def index_document_job(job: dict) -> str:
    """Index an uploaded document version; it stays unpublished ("ready") until a knowledge manager publishes it."""
    from pypdf.errors import PdfReadError

    from . import rag, storage  # Only indexing jobs need the PDF reader and embedding client.

    payload, company_id = job["payload"], job["company_id"]
    document_filter = {"id": f"eq.{payload['document_id']}", "status": "eq.processing"}
    try:
        with storage.original_file(payload["file_key"]) as path:
            chunks = rag.index_document(
                path, company_id=company_id, source_id=payload["source_id"], version=payload["version"],
                title=payload["title"], venue=payload.get("venue", ""),
            )
    except Exception as exc:
        # Leave no partial chunks behind; the version can be uploaded again.
        database.run_raw_sql(
            "DELETE FROM knowledge_chunks WHERE company_id = $1 AND source_id = $2 AND version = $3;",
            [company_id, payload["source_id"], payload["version"]],
        )
        unreadable = isinstance(exc, (ValueError, PdfReadError))
        error = "No readable text could be extracted from this file." if unreadable else "Indexing failed. Upload the file again."
        database.update_records("documents", {"status": "failed", "error": error}, **document_filter)
        set_job_status(job["id"], "failed", last_error=f"{type(exc).__name__}: {exc}"[:1000])
        return "failed"
    database.update_records("documents", {"status": "ready", "error": None}, **document_filter)
    database.log_audit_event(
        company_id=company_id, actor_id="system:indexer", action="document_indexed", target_type="document",
        target_id=payload["document_id"], details={"chunks": chunks},
    )
    set_job_status(job["id"], "completed")
    return "completed"


def run_job(job_id: str) -> str:
    """Claim and run one job; returns its final status."""
    jobs = database.query_records("jobs", id=f"eq.{job_id}", limit=1)
    if not jobs:
        raise ValueError(f"Job {job_id} not found.")
    job = jobs[0]
    # Conditional claim: a redelivered or concurrent invocation cannot run the same job twice.
    if not database.update_records(
        "jobs",
        {"status": "running", "attempts": job["attempts"] + 1, "updated_at": datetime.now(UTC).isoformat()},
        id=f"eq.{job_id}",
        status="in.(queued,dispatched)",
    ):
        return "already_claimed"

    try:
        if job["kind"] == "index":
            return index_document_job(job)
        if job["kind"] == "resume":
            context, agent_input = build_resume(job)
        else:
            start = build_start(job)
            if start is None:
                set_job_status(job_id, "skipped")
                return "skipped"
            context, message = start
            agent_input = {"messages": [{"role": "user", "content": message}]}

        result = get_agent().invoke(
            agent_input, config={"configurable": {"thread_id": job["thread_id"]}}, context=context, version="v2"
        )
        if result.interrupts:
            record_interrupts(job, result.interrupts)
            status = "awaiting_review"
        else:
            status = "completed"
        set_job_status(job_id, status)
        return status
    except Exception as exc:
        set_job_status(job_id, "failed", last_error=f"{type(exc).__name__}: {exc}"[:1000])
        raise


def _run_tracked(job_id: str, task_id: int) -> None:
    try:
        run_job(job_id)
    finally:
        # Ends HEALTHY_BUSY so AgentCore can idle out the session.
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Acknowledge the job at once and run it in a tracked background task."""
    # Payloads arrive as parsed JSON of any shape; accept only {"job_id": "<uuid string>"}.
    raw_id = payload.get("job_id") if isinstance(payload, dict) else None
    try:
        job_id = str(UUID(raw_id)) if isinstance(raw_id, str) else ""
    except ValueError:
        job_id = ""
    if not job_id:
        return {"status": "rejected", "error": "A valid job_id is required."}
    task_id = app.add_async_task("run_job", {"job_id": job_id})
    threading.Thread(target=_run_tracked, args=(job_id, task_id), daemon=True).start()
    return {"status": "accepted", "job_id": job_id}


if __name__ == "__main__":
    app.run()

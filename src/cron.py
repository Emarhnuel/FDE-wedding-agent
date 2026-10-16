"""Scheduled work invoked by EventBridge Scheduler through Lambda.

Two schedules target this Lambda:
- every 8 minutes with input {"task": "poll_gmail"}: queue new emails from the shared inbox;
- daily (any other input): retention selection, stuck-job recovery, and the 120-day purge.

Everything here only queues jobs or deletes old data (the agent never runs here), and is safe to
run twice: Scheduler delivers at least once, and each job's dedupe key stops duplicates.
"""

import base64
import os
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

import httpx
from langgraph.checkpoint.postgres import PostgresSaver

from . import database
from .api import COMPANY_ID, send_job
from .tools.retention_tools import check_couple_eligibility
from .tools.supervisor_tools import get_gmail_access_token

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SENDER = os.environ.get("GMAIL_SENDER", "").lower()
AUTOMATED_SENDERS = {"mailer-daemon", "postmaster", "no-reply", "noreply", "do-not-reply", "donotreply"}
DATABASE_URL = os.environ.get("DATABASE_URL", "")
RETENTION_DAYS = 120
VENUE_TIMEZONE = ZoneInfo(os.environ.get("VENUE_TIMEZONE", "America/Chicago"))
ANNIVERSARY_LEAD_DAYS = 7
REFERRAL_AFTER_DAYS = 90
REFERRAL_CATCH_UP_DAYS = 14  # Still queue a referral if a few daily runs were missed.
STALE_DISPATCH = timedelta(minutes=30)  # Dispatched but never claimed by the runtime.
STALE_RUN = timedelta(hours=9)  # Past AgentCore's 8-hour session maximum.


def due_occasion(wedding: date, today: date) -> tuple[str, str] | None:
    """Return (occasion, dedupe suffix) when a couple is due for outreach today."""
    if REFERRAL_AFTER_DAYS <= (today - wedding).days <= REFERRAL_AFTER_DAYS + REFERRAL_CATCH_UP_DAYS:
        return "referral", "referral"
    for year in (today.year, today.year + 1):
        try:
            anniversary = wedding.replace(year=year)
        except ValueError:  # 29 February in a non-leap year.
            anniversary = date(year, 2, 28)
        if year > wedding.year and 0 <= (anniversary - today).days <= ANNIVERSARY_LEAD_DAYS:
            return "anniversary", f"anniversary:{year}"
    return None


def queue_retention_jobs(today: date) -> int:
    """Queue one retention job per eligible couple and due occasion."""
    queued = 0
    for couple in database.get_eligible_couples(COMPANY_ID):
        if not couple.get("wedding_date"):
            continue
        due = due_occasion(date.fromisoformat(couple["wedding_date"]), today)
        # Full rule check (also pending drafts) only for couples with something due.
        if not due or not check_couple_eligibility(couple, company_id=COMPANY_ID)[0]:
            continue
        occasion, suffix = due
        key = f"retention:{couple['contact_id']}:{suffix}"
        job, created = database.enqueue_job(
            company_id=COMPANY_ID, kind="retention", thread_id=key, dedupe_key=key,
            payload={"contact_id": couple["contact_id"], "occasion": occasion},
        )
        if created:
            send_job(job)
            queued += 1
    return queued


def recover_stuck_jobs(now: datetime) -> dict:
    """Re-queue jobs the runtime never claimed; fail runs whose runtime session has ended."""
    requeued = failed = 0
    stale_dispatch = (now - STALE_DISPATCH).isoformat()
    for job in database.query_records("jobs", status="eq.dispatched", updated_at=f"lt.{stale_dispatch}"):
        if database.update_records("jobs", {"status": "queued", "updated_at": now.isoformat()},
                                   id=f"eq.{job['id']}", status="eq.dispatched"):
            send_job(job, dedupe_id=f"{job['id']}:{job['attempts']}:{now.date()}")
            requeued += 1
    # A run cut off mid-way may already have sent email, so it is never retried automatically.
    for job in database.query_records("jobs", status="eq.running", updated_at=f"lt.{(now - STALE_RUN).isoformat()}"):
        failed += bool(database.update_records(
            "jobs",
            {"status": "failed", "last_error": "Runtime session ended before the job finished; staff must review.",
             "updated_at": now.isoformat()},
            id=f"eq.{job['id']}", status="eq.running",
        ))
    return {"requeued": requeued, "failed_stuck_runs": failed}


def gmail_get(token: str, path: str, **params) -> dict:
    response = httpx.get(f"{GMAIL_API}/{path}", headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15.0)
    response.raise_for_status()
    return response.json()


def new_inbox_message_ids(token: str) -> tuple[list[str], str]:
    """Return inbox message IDs added since the stored history position, and the new position."""
    cursor = database.get_sync_state("gmail_history_id")
    if cursor:
        ids, page = [], {}
        try:
            while True:
                data = gmail_get(token, "history", startHistoryId=cursor, historyTypes="messageAdded",
                                 labelId="INBOX", **page)
                ids += [added["message"]["id"] for h in data.get("history", []) for added in h.get("messagesAdded", [])]
                if not data.get("nextPageToken"):
                    return ids, data["historyId"]
                page = {"pageToken": data["nextPageToken"]}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
    # First run starts from now; an expired position (Gmail 404) falls back to a full sync of recent mail.
    latest = gmail_get(token, "profile")["historyId"]
    if not cursor:
        return [], latest
    recent = gmail_get(token, "messages", q="in:inbox newer_than:7d", maxResults=500).get("messages", [])
    return [m["id"] for m in recent], latest


def plain_text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    return next((text for sub in part.get("parts", []) if (text := plain_text(sub))), "")


def parse_inquiry(message: dict) -> dict | None:
    """Turn a Gmail message into an inquiry payload; None for our own, automated, or bulk mail."""
    headers = {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}
    name, sender = parseaddr(headers.get("from", ""))
    sender = sender.lower()
    if (
        not sender
        or sender == GMAIL_SENDER
        or "SENT" in message.get("labelIds", [])
        or sender.split("@")[0] in AUTOMATED_SENDERS
        or headers.get("auto-submitted", "no").lower() != "no"
        or headers.get("precedence", "").lower() in {"bulk", "junk", "list"}
    ):
        return None
    return {
        "source": "gmail",
        "name": name or sender,
        "email": sender,
        "subject": headers.get("subject", ""),
        "message": (plain_text(message["payload"]) or message.get("snippet", ""))[:5000],
        "message_id_header": headers.get("message-id", ""),
        "gmail_thread_id": message["threadId"],
        "gmail_message_id": message["id"],
    }


def poll_gmail() -> int:
    """Queue one inquiry job per new customer email; each Gmail thread is one agent conversation."""
    token = get_gmail_access_token()
    ids, latest = new_inbox_message_ids(token)
    queued = 0
    for message_id in dict.fromkeys(ids):
        try:
            inquiry = parse_inquiry(gmail_get(token, f"messages/{message_id}", format="full"))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # Deleted since it arrived.
                continue
            raise
        if inquiry:
            job, created = database.enqueue_job(
                company_id=COMPANY_ID, kind="inquiry", thread_id=f"gmail:{inquiry['gmail_thread_id']}",
                dedupe_key=f"gmail:{message_id}", payload=inquiry,
            )
            if created:
                send_job(job)
                queued += 1
    # Advance the position only after every message is queued, so a failed run is retried.
    database.set_sync_state("gmail_history_id", latest)
    return queued


def purge_old_conversations(now: datetime) -> int:
    """Delete conversation data older than 120 days: agent checkpoints, job payloads, review rows.

    email_drafts and audit_logs are kept as the business record of what was sent and approved.
    """
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    old = database.query_records("jobs", company_id=f"eq.{COMPANY_ID}", updated_at=f"lt.{cutoff}",
                                 status="not.in.(queued,dispatched,running)", select="thread_id")
    recent = database.query_records("jobs", company_id=f"eq.{COMPANY_ID}", updated_at=f"gte.{cutoff}", select="thread_id")
    # Only threads with no recent activity lose their saved conversation state.
    idle_threads = {j["thread_id"] for j in old} - {j["thread_id"] for j in recent}
    if idle_threads and DATABASE_URL:
        with PostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:
            for thread_id in idle_threads:
                checkpointer.delete_thread(thread_id)
    database.run_raw_sql(
        f"DELETE FROM agent_reviews WHERE company_id = '{COMPANY_ID}' AND created_at < '{cutoff}';"
    )
    database.run_raw_sql(
        f"DELETE FROM jobs WHERE company_id = '{COMPANY_ID}' AND updated_at < '{cutoff}' "
        "AND status NOT IN ('queued', 'dispatched', 'running');"
    )
    return len(idle_threads)


def handler(event, _context) -> dict:
    """Lambda entry point for both EventBridge Scheduler schedules."""
    if isinstance(event, dict) and event.get("task") == "poll_gmail":
        return {"gmail_inquiries_queued": poll_gmail()}
    now = datetime.now(UTC)
    return {"retention_jobs_queued": queue_retention_jobs(now.astimezone(VENUE_TIMEZONE).date()),
            **recover_stuck_jobs(now),
            "threads_purged": purge_old_conversations(now)}

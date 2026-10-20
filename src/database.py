import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

INSFORGE_URL = os.environ.get("INSFORGE_URL", "").rstrip("/")
INSFORGE_API_KEY = os.environ.get("INSFORGE_API_KEY", "")
# One shared, pooled HTTP client for InsForge, HubSpot and Gmail. Reusing connections makes each
# call ~0.3 s instead of ~1.3 s for a fresh TLS connection. httpx.Client is safe to share across threads.
http = httpx.Client(timeout=15.0)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS staff_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    insforge_user_id TEXT UNIQUE NOT NULL,
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    email TEXT NOT NULL,
    full_name TEXT,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    thread_id TEXT NOT NULL,
    contact_id TEXT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    job_type TEXT NOT NULL DEFAULT 'inquiry',
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    requires_review BOOLEAN NOT NULL DEFAULT true,
    review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending_review',
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS couples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    contact_id TEXT UNIQUE NOT NULL,
    couple_names TEXT NOT NULL,
    email TEXT NOT NULL,
    venue TEXT,
    wedding_date DATE,
    marketing_consent BOOLEAN NOT NULL DEFAULT false,
    do_not_contact BOOLEAN NOT NULL DEFAULT false,
    has_open_complaint BOOLEAN NOT NULL DEFAULT false,
    last_outreach_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    source_id TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT,
    venue TEXT,
    status TEXT NOT NULL DEFAULT 'staged',
    uploaded_by TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, source_id, version)
);

CREATE EXTENSION IF NOT EXISTS vector;

-- RAG chunks. Searchable only while their documents row is 'published' (see rag.search_knowledge).
-- No ANN index yet: exact search is fast at this size, and a filtered HNSW scan can return too few rows.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    version TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    page INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    thread_id TEXT NOT NULL,
    interrupt_id TEXT NOT NULL,
    action_index INTEGER NOT NULL,
    action_name TEXT NOT NULL,
    action_args JSONB NOT NULL DEFAULT '{}'::jsonb,
    draft_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    decision JSONB,
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (interrupt_id, action_index)
);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL DEFAULT 'juniper-hollow',
    kind TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    dedupe_key TEXT UNIQUE NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    runtime_session_id TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _headers(*, prefer: str | None = None) -> dict[str, str]:
    if not INSFORGE_URL or not INSFORGE_API_KEY:
        raise RuntimeError("INSFORGE_URL and INSFORGE_API_KEY must be set in .env")
    headers = {
        "Authorization": f"Bearer {INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def run_raw_sql(query: str, params: list[Any] | None = None) -> dict[str, Any]:
    """Execute a raw SQL query against InsForge PostgreSQL."""
    payload: dict[str, Any] = {"query": query}
    if params is not None:
        payload["params"] = params
    response = http.post(
        f"{INSFORGE_URL}/api/database/advance/rawsql",
        headers=_headers(),
        json=payload,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def init_schema() -> None:
    """Create all application tables in InsForge if they do not exist."""
    for statement in (stmt.strip() for stmt in SCHEMA_SQL.split(";") if stmt.strip()):
        run_raw_sql(f"{statement};")


def query_records(table: str, **filters: str | int) -> list[dict[str, Any]]:
    """Query rows from an InsForge table using PostgREST filters."""
    response = http.get(
        f"{INSFORGE_URL}/api/database/records/{table}",
        headers=_headers(),
        params=filters,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def insert_record(table: str, record: dict[str, Any], *, upsert: bool = False) -> dict[str, Any]:
    """Insert (or upsert) a single row into an InsForge table and return it."""
    prefer = "resolution=merge-duplicates,return=representation" if upsert else "return=representation"
    response = http.post(
        f"{INSFORGE_URL}/api/database/records/{table}",
        headers=_headers(prefer=prefer),
        json=[record],
        timeout=15.0,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else record


def update_records(table: str, updates: dict[str, Any], **filters: str) -> list[dict[str, Any]]:
    """Update rows matching PostgREST filters and return the updated records."""
    response = http.patch(
        f"{INSFORGE_URL}/api/database/records/{table}",
        headers=_headers(prefer="return=representation"),
        params=filters,
        json=updates,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def get_staff_member(insforge_user_id: str, company_id: str = "juniper-hollow") -> dict[str, Any] | None:
    """Fetch an active staff member record scoped to the company."""
    rows = query_records(
        "staff_members",
        insforge_user_id=f"eq.{insforge_user_id}",
        company_id=f"eq.{company_id}",
        is_active="is.true",
        limit=1,
    )
    return rows[0] if rows else None


def save_email_draft(
    *,
    company_id: str,
    thread_id: str,
    recipient: str,
    subject: str,
    body: str,
    job_type: str = "inquiry",
    contact_id: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    requires_review: bool = True,
    review_reasons: list[str] | None = None,
    status: str = "pending_review",
) -> dict[str, Any]:
    """Persist an email draft and its citations for review or delivery."""
    return insert_record(
        "email_drafts",
        {
            "company_id": company_id,
            "thread_id": thread_id,
            "contact_id": contact_id,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "job_type": job_type,
            "sources": sources or [],
            "requires_review": requires_review,
            "review_reasons": review_reasons or [],
            "status": status,
        },
    )


def get_email_draft(draft_id: str, company_id: str = "juniper-hollow") -> dict[str, Any] | None:
    """Fetch a single email draft by ID and company scope."""
    rows = query_records("email_drafts", id=f"eq.{draft_id}", company_id=f"eq.{company_id}", limit=1)
    return rows[0] if rows else None


def update_draft_status(
    draft_id: str,
    *,
    company_id: str,
    status: str,
    reviewed_by: str | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    """Record a staff approval/edit/rejection or sent status on a draft."""
    now = datetime.now(UTC).isoformat()
    updates: dict[str, Any] = {"status": status}
    if reviewed_by:
        updates["reviewed_by"] = reviewed_by
        updates["reviewed_at"] = now
    if body is not None:
        updates["body"] = body
    if status == "sent":
        updates["sent_at"] = now
    rows = update_records("email_drafts", updates, id=f"eq.{draft_id}", company_id=f"eq.{company_id}")
    return rows[0] if rows else None


def get_eligible_couples(company_id: str = "juniper-hollow", cooldown_days: int = 90) -> list[dict[str, Any]]:
    """Return past couples eligible for retention outreach."""
    rows = query_records(
        "couples",
        company_id=f"eq.{company_id}",
        marketing_consent="is.true",
        do_not_contact="is.false",
        has_open_complaint="is.false",
    )
    cutoff = datetime.now(UTC) - timedelta(days=cooldown_days)
    eligible: list[dict[str, Any]] = []
    for row in rows:
        last_sent = row.get("last_outreach_at")
        if not last_sent or datetime.fromisoformat(last_sent.replace("Z", "+00:00")) <= cutoff:
            eligible.append(row)
    return eligible


def enqueue_job(
    *,
    company_id: str,
    kind: str,
    thread_id: str,
    dedupe_key: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Durably record a job once per dedupe_key; returns (job, created) so repeats are not re-queued."""
    try:
        job = insert_record(
            "jobs",
            {
                "company_id": company_id,
                "kind": kind,
                "thread_id": thread_id,
                "dedupe_key": dedupe_key,
                "payload": payload or {},
            },
        )
        return job, True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        return query_records("jobs", dedupe_key=f"eq.{dedupe_key}", limit=1)[0], False


def get_sync_state(key: str) -> str | None:
    """Read a stored integration cursor, such as the Gmail history position."""
    rows = query_records("sync_state", key=f"eq.{key}", limit=1)
    return rows[0]["value"] if rows else None


def set_sync_state(key: str, value: str) -> None:
    insert_record("sync_state", {"key": key, "value": value, "updated_at": datetime.now(UTC).isoformat()}, upsert=True)


def log_audit_event(
    *,
    company_id: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an audit log entry for staff reviews, deliveries, or knowledge changes."""
    return insert_record(
        "audit_logs",
        {
            "company_id": company_id,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details or {},
        },
    )

"""End to end through the real entry points: API → job → runtime → staff review → resume → Gmail.

Every step uses live services and no mocks:
- the FastAPI app (in-process `TestClient`) authenticated by a real InsForge staff account;
- `runtime.run_job`, the same function AgentCore runs, with the real model, Postgres checkpoints and RAG;
- real HubSpot and real Gmail sends to TEST_EMAIL_RECIPIENT (defaults to GMAIL_SENDER).

Only the AWS hops are skipped: with no JOBS_QUEUE_URL the API records jobs without SQS, and the test
calls `run_job` directly where the worker would invoke AgentCore. Costs model tokens and sends real email.
Inquiry replies pause once, at the send step, unless AUTO_SEND_ROUTINE lets a routine reply go out.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from src import api, cron, database, middleware, rag, runtime
from src.tools.inquiry_tools import HUBSPOT_BASE_URL, _hubspot_headers, find_hubspot_contact_by_email
from src.tools.supervisor_tools import get_gmail_access_token

PDF_PATH = Path(__file__).resolve().parent.parent / "juniper-hollow-knowledge-pack.pdf"
COMPANY_ID = f"test-e2e-{uuid4().hex[:8]}"
SOURCE_ID, SOURCE_VERSION = "JH-KB-2026-03", "3.0"
RECIPIENT = os.environ.get("TEST_EMAIL_RECIPIENT") or os.environ.get("GMAIL_SENDER", "")
MAX_REVIEW_ROUNDS = 8


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {database.INSFORGE_API_KEY}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    """Isolated company, published knowledge, a real email_approver login, and full cleanup."""
    assert RECIPIENT, "Set TEST_EMAIL_RECIPIENT or GMAIL_SENDER in .env"
    assert os.environ.get("DATABASE_URL"), "Set DATABASE_URL so paused runs persist in Postgres"
    database.init_schema()

    # Trusted server config points at the throwaway company; no SQS, and the test runs each job itself.
    api.COMPANY_ID = cron.COMPANY_ID = COMPANY_ID
    api.JOBS_QUEUE_URL = ""
    api.RUN_JOBS_LOCALLY = False
    middleware.AUTO_SEND_ROUTINE = False

    rag.index_document(
        PDF_PATH, company_id=COMPANY_ID, source_id=SOURCE_ID, version=SOURCE_VERSION,
        title="Juniper Hollow Company Knowledge Pack",
    )
    database.insert_record(
        "documents", {"company_id": COMPANY_ID, "source_id": SOURCE_ID, "version": SOURCE_VERSION, "status": "published"}
    )

    email, password = f"approver.{uuid4().hex[:8]}@juniperhollow-test.com", f"Test-{uuid4().hex}"
    created = httpx.post(
        f"{database.INSFORGE_URL}/api/auth/users", params={"client_type": "server"}, headers=_admin_headers(),
        json={"email": email, "password": password, "name": "E2E Approver"}, timeout=15.0,
    )
    assert created.status_code in (200, 201), f"Could not create staff login: {created.status_code} {created.text}"
    user = created.json()
    database.insert_record(
        "staff_members",
        {"insforge_user_id": user["user"]["id"], "company_id": COMPANY_ID,
         "email": email, "full_name": "E2E Approver", "roles": ["email_approver"]},
    )
    preexisting_contact = find_hubspot_contact_by_email(RECIPIENT)

    test_client = TestClient(api.app)
    test_client.staff_login = {"username": email, "password": password}
    try:
        yield test_client
    finally:
        agent = runtime.get_agent()
        for job in database.query_records("jobs", company_id=f"eq.{COMPANY_ID}", select="thread_id"):
            agent.checkpointer.delete_thread(job["thread_id"])
        for item in agent.store.search((COMPANY_ID,), limit=1000):
            agent.store.delete(item.namespace, item.key)
        for table in ("jobs", "agent_reviews", "email_drafts", "couples", "documents", "audit_logs", "staff_members",
                      "knowledge_chunks"):
            database.run_raw_sql(f"DELETE FROM {table} WHERE company_id = '{COMPANY_ID}';")
        httpx.request("DELETE", f"{database.INSFORGE_URL}/api/auth/users", headers=_admin_headers(),
                      json={"userIds": [user["user"]["id"]]}, timeout=15.0)
        if not preexisting_contact and (contact := find_hubspot_contact_by_email(RECIPIENT)):
            httpx.delete(f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact['id']}",
                         headers=_hubspot_headers(), timeout=15.0)


def job(job_id: str) -> dict:
    return database.query_records("jobs", id=f"eq.{job_id}", limit=1)[0]


def run_with_staff(client: TestClient, job_id: str, decide) -> list[dict]:
    """Run a job; answer each pause through the review API with decide(review); return the reviews seen.

    decide returns the JSON body for POST /reviews/{id}/decision.
    """
    thread_id = job(job_id)["thread_id"]
    seen: list[dict] = []
    for _ in range(MAX_REVIEW_ROUNDS):
        status = runtime.run_job(job_id)
        print(f"  job {job(job_id)['kind']:<9} → {status}")
        if status == "completed":
            return seen
        assert status == "awaiting_review", f"Job ended as {status}: {job(job_id).get('last_error')}"

        # Staff logins last 15 minutes and agent runs can take longer, so sign in fresh each round.
        login = client.post("/auth/login", data=client.staff_login)
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        pending = [r for r in client.get("/reviews").json() if r["thread_id"] == thread_id]
        assert pending, "Job paused but the review queue is empty"
        resume_job_id = None
        for review in pending:
            detail = client.get(f"/reviews/{review['id']}").json()
            seen.append(detail)
            body = decide(detail)
            print(f"  staff {body['decision']:<7} {detail['action_name']}")
            response = client.post(f"/reviews/{review['id']}/decision", json=body)
            assert response.status_code == 200, response.text
            resume_job_id = response.json()["resume_job_id"] or resume_job_id
        assert resume_job_id, "Every review was decided but no resume job was queued"
        job_id = resume_job_id
    pytest.fail(f"Still paused after {MAX_REVIEW_ROUNDS} review rounds")


def approve(_review: dict) -> dict:
    return {"decision": "approve"}


def drafts(thread_id: str) -> list[dict]:
    return database.query_records("email_drafts", company_id=f"eq.{COMPANY_ID}", thread_id=f"eq.{thread_id}")


def audit(draft_id: str) -> dict[str, dict]:
    rows = database.query_records("audit_logs", company_id=f"eq.{COMPANY_ID}", target_id=f"eq.{draft_id}")
    return {row["action"]: row for row in rows}


def assert_really_sent(draft: dict) -> None:
    """The audit log's Gmail message id must exist in the venue mailbox as a sent email to the recipient."""
    message_id = audit(draft["id"])["email_sent"]["details"]["gmail_message_id"]
    message = httpx.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        params={"format": "metadata", "metadataHeaders": ["To", "Subject"]},
        headers={"Authorization": f"Bearer {get_gmail_access_token()}"}, timeout=15.0,
    ).json()
    headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
    assert "SENT" in message["labelIds"]
    assert RECIPIENT.lower() in headers["To"].lower()
    assert headers["Subject"] == draft["subject"]
    print(f"  Gmail sent: '{headers['Subject']}' → {RECIPIENT}")


def submit(client: TestClient, message: str) -> str:
    response = client.post("/inquiries", json={"name": "Jordan Avery", "email": RECIPIENT, "message": message})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return response.json()["job_id"]


def test_routine_web_inquiry_is_reviewed_once_and_sent(client):
    job_id = submit(client, f"Hi! Do you have Wi-Fi for guests, and is there parking on site? [{uuid4().hex[:6]}]")
    thread_id = job(job_id)["thread_id"]

    reviews = run_with_staff(client, job_id, approve)

    # With automatic sending off, the single review is at the send step.
    assert [r["action_name"] for r in reviews] == ["send_customer_email"]
    sent = [d for d in drafts(thread_id) if d["status"] == "sent"]
    assert len(sent) == 1, f"Draft statuses: {[d['status'] for d in drafts(thread_id)]}"
    assert "review_approve" in {row["action"] for row in database.query_records(
        "audit_logs", company_id=f"eq.{COMPANY_ID}", target_type="eq.agent_review")}
    assert_really_sent(sent[0])


def test_routine_reply_backed_by_sources_sends_itself_when_auto_send_is_on(client, monkeypatch):
    monkeypatch.setattr(middleware, "AUTO_SEND_ROUTINE", True)
    job_id = submit(client, f"Hello, is there Wi-Fi at the Glasshouse? [{uuid4().hex[:6]}]")
    thread_id = job(job_id)["thread_id"]

    reviews = run_with_staff(client, job_id, approve)

    assert reviews == [], "A routine, source-backed reply must not wait for staff"
    sent = [d for d in drafts(thread_id) if d["status"] == "sent"]
    assert len(sent) == 1 and sent[0]["sources"] and not sent[0]["requires_review"]
    assert not sent[0]["reviewed_by"]
    assert_really_sent(sent[0])


def test_pricing_inquiry_is_held_until_staff_approve_the_send(client, monkeypatch):
    # Automatic sending is on, yet a restricted topic must still wait for staff.
    monkeypatch.setattr(middleware, "AUTO_SEND_ROUTINE", True)
    job_id = submit(client, "What is the Saturday price for the barn in October 2027, and can you hold Oct 16 for us?")
    thread_id = job(job_id)["thread_id"]
    held: list[dict] = []

    def decide(review: dict) -> dict:
        if review["action_name"] == "send_customer_email":
            held.append(review["draft"])
        return approve(review)

    run_with_staff(client, job_id, decide)

    assert held, "A pricing reply must pause at the send step"
    assert held[0]["requires_review"] is True and held[0]["status"] == "pending_review"
    sent = [d for d in drafts(thread_id) if d["status"] == "sent"]
    assert len(sent) == 1 and sent[0]["reviewed_by"]
    assert_really_sent(sent[0])


def test_staff_edit_is_what_gets_sent(client):
    marker = f"Edited by staff {uuid4().hex[:6]}."
    job_id = submit(client, "Can you hold Saturday June 12, 2027 for our wedding of about 120 guests?")
    thread_id = job(job_id)["thread_id"]

    def decide(review: dict) -> dict:
        if review["action_name"] == "send_customer_email":
            return {"decision": "edit", "body": f"{review['draft']['body']}\n\n{marker}"}
        return approve(review)

    run_with_staff(client, job_id, decide)

    sent = [d for d in drafts(thread_id) if d["status"] == "sent"]
    assert len(sent) == 1, f"Draft statuses: {[d['status'] for d in drafts(thread_id)]}"
    assert sent[0]["body"].endswith(marker)
    assert_really_sent(sent[0])


def test_rejected_reply_is_never_sent(client):
    job_id = submit(client, "We're only 40 people. Can you give us a 30% discount on the Glasshouse?")
    thread_id = job(job_id)["thread_id"]

    def decide(review: dict) -> dict:
        if review["action_name"].startswith("send_"):
            return {"decision": "reject", "message": "Staff will reply personally; do not send."}
        return approve(review)

    run_with_staff(client, job_id, decide)

    assert drafts(thread_id), "The agent should still save a draft for staff"
    assert all(d["status"] != "sent" and "email_sent" not in audit(d["id"]) for d in drafts(thread_id))


def test_retention_from_cron_is_reviewed_sent_and_starts_cooldown(client):
    today = datetime.now(UTC).astimezone(cron.VENUE_TIMEZONE).date()
    contact_id = f"couple-{uuid4().hex[:8]}"
    database.insert_record(
        "couples",
        {"company_id": COMPANY_ID, "contact_id": contact_id, "couple_names": "Ava & Elena", "email": RECIPIENT,
         "venue": "Hollow Brook Barn", "wedding_date": (today.replace(year=today.year - 1) + timedelta(days=3)).isoformat(),
         "marketing_consent": True, "do_not_contact": False, "has_open_complaint": False},
    )
    started = datetime.now(UTC)

    assert cron.queue_retention_jobs(today) == 1, "The cron sweep should queue the anniversary couple"
    job_id = database.query_records("jobs", company_id=f"eq.{COMPANY_ID}", kind="eq.retention")[0]["id"]
    thread_id = job(job_id)["thread_id"]

    reviews = run_with_staff(client, job_id, approve)

    names = [r["action_name"] for r in reviews]
    assert "save_retention_draft" in names and "send_retention_email" in names, names
    sent = [d for d in drafts(thread_id) if d["status"] == "sent"]
    assert len(sent) == 1 and sent[0]["job_type"] == "retention"
    assert_really_sent(sent[0])

    couple = database.query_records("couples", company_id=f"eq.{COMPANY_ID}", contact_id=f"eq.{contact_id}")[0]
    assert datetime.fromisoformat(couple["last_outreach_at"].replace("Z", "+00:00")) >= started - timedelta(seconds=5)
    assert cron.queue_retention_jobs(today) == 0, "Cooldown must stop a second outreach"

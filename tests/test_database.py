from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src import database


@pytest.fixture(scope="module", autouse=True)
def setup_insforge_tables():
    database.init_schema()
    yield
    for table in ("staff_members", "email_drafts", "couples", "documents", "audit_logs"):
        database.run_raw_sql(f"DELETE FROM {table} WHERE company_id LIKE 'test-company-%';")


def test_staff_member_insert_and_lookup():
    company_id = f"test-company-{uuid4().hex[:8]}"
    user_id = f"user-{uuid4().hex[:8]}"

    created = database.insert_record(
        "staff_members",
        {
            "insforge_user_id": user_id,
            "company_id": company_id,
            "email": "maya@juniperhollow.com",
            "full_name": "Maya Okafor",
            "roles": ["email_approver", "knowledge_manager"],
            "is_active": True,
        },
    )
    assert created["insforge_user_id"] == user_id

    fetched = database.get_staff_member(user_id, company_id=company_id)
    assert fetched is not None
    assert fetched["email"] == "maya@juniperhollow.com"
    assert "email_approver" in fetched["roles"]
    assert database.get_staff_member(user_id, company_id="other-company") is None


def test_email_draft_save_and_staff_approval():
    company_id = f"test-company-{uuid4().hex[:8]}"
    draft = database.save_email_draft(
        company_id=company_id,
        thread_id="thread-101",
        contact_id="contact-55",
        recipient="couple@example.com",
        subject="Saturday Rental Inquiry",
        body="Hollow Brook Barn Saturday peak rental is $7,800.",
        job_type="inquiry",
        sources=[{"source_id": "JH-KB-2026-03", "version": "3.0", "page": 6}],
        requires_review=True,
        review_reasons=["pricing"],
    )
    draft_id = draft["id"]
    assert draft["status"] == "pending_review"

    fetched = database.get_email_draft(draft_id, company_id=company_id)
    assert fetched is not None
    assert fetched["recipient"] == "couple@example.com"

    updated = database.update_draft_status(
        draft_id,
        company_id=company_id,
        status="sent",
        reviewed_by="maya@juniperhollow.com",
    )
    assert updated is not None
    assert updated["status"] == "sent"
    assert updated["reviewed_by"] == "maya@juniperhollow.com"
    assert updated["sent_at"] is not None


def test_eligible_couples_filters_consent_and_cooldown():
    company_id = f"test-company-{uuid4().hex[:8]}"
    eligible_contact = f"contact-eligible-{uuid4().hex[:8]}"
    recent_contact = f"contact-recent-{uuid4().hex[:8]}"
    no_consent_contact = f"contact-noconsent-{uuid4().hex[:8]}"

    database.insert_record(
        "couples",
        {
            "company_id": company_id,
            "contact_id": eligible_contact,
            "couple_names": "Alex & Sam",
            "email": "alex.sam@example.com",
            "venue": "Hollow Brook Barn",
            "wedding_date": "2025-06-14",
            "marketing_consent": True,
            "do_not_contact": False,
            "has_open_complaint": False,
            "last_outreach_at": (datetime.now(UTC) - timedelta(days=120)).isoformat(),
        },
    )
    database.insert_record(
        "couples",
        {
            "company_id": company_id,
            "contact_id": recent_contact,
            "couple_names": "Jordan & Taylor",
            "email": "jordan.taylor@example.com",
            "venue": "The Juniper Glasshouse",
            "wedding_date": "2025-09-20",
            "marketing_consent": True,
            "do_not_contact": False,
            "has_open_complaint": False,
            "last_outreach_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        },
    )
    database.insert_record(
        "couples",
        {
            "company_id": company_id,
            "contact_id": no_consent_contact,
            "couple_names": "Casey & Morgan",
            "email": "casey.morgan@example.com",
            "venue": "Hollow Brook Barn",
            "wedding_date": "2025-05-10",
            "marketing_consent": False,
            "do_not_contact": False,
            "has_open_complaint": False,
        },
    )

    eligible = database.get_eligible_couples(company_id=company_id, cooldown_days=90)
    contact_ids = [row["contact_id"] for row in eligible]
    assert contact_ids == [eligible_contact]


def test_audit_log_creation():
    company_id = f"test-company-{uuid4().hex[:8]}"
    entry = database.log_audit_event(
        company_id=company_id,
        actor_id="maya@juniperhollow.com",
        action="approve_and_send",
        target_type="email_draft",
        target_id="draft-101",
        details={"version": "3.0"},
    )
    assert entry["action"] == "approve_and_send"
    assert entry["actor_id"] == "maya@juniperhollow.com"

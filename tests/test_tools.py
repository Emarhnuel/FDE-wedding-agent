from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src import database
from src.schemas import AgentContext
from src.tools.inquiry_tools import (
    HUBSPOT_BASE_URL,
    _hubspot_headers,
    get_inquiry_history,
    save_inquiry_draft,
)
from src.tools.retention_tools import get_couple_history, save_retention_draft


@pytest.fixture(scope="module", autouse=True)
def setup_and_cleanup_insforge():
    database.init_schema()
    yield
    for table in ("email_drafts", "couples"):
        database.run_raw_sql(f"DELETE FROM {table} WHERE company_id LIKE 'test-tools-%';")


def test_inquiry_tools_hubspot_upsert_and_history():
    tag = uuid4().hex[:8]
    company_id = f"test-tools-{tag}"
    test_email = f"liam.noah.{tag}@juniperhollow.com"
    runtime = SimpleNamespace(
        context=AgentContext(company_id=company_id, conversation_id=f"thread-{tag}")
    )

    contact_id = None
    try:
        save_result = save_inquiry_draft.func(
            recipient=test_email,
            subject="Hollow Brook Barn Saturday Rate & Wi-Fi",
            body="Hollow Brook Barn has 50 Mbps Wi-Fi, and peak Saturday rental is $7,800.",
            runtime=runtime,
            couple_names="Liam Noah",
            phone="319-555-0199",
            requires_review=False,
            sources=[{"source_id": "JH-KB-2026-03", "version": "3.0", "page": 6}],
        )
        assert "status=pending_review" in save_result
        assert "requires_review=True" in save_result

        for part in save_result.split(","):
            if "contact_id=" in part:
                contact_id = part.split("contact_id=")[1].strip(" ]")

        assert contact_id is not None

        history_result = get_inquiry_history.func(test_email, runtime=runtime)
        assert f"HubSpot Contact [id={contact_id}]" in history_result
        assert "Liam Noah" in history_result
        assert "Prior drafts:" in history_result
        assert "Hollow Brook Barn Saturday Rate & Wi-Fi" in history_result
    finally:
        if contact_id:
            httpx.delete(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                headers=_hubspot_headers(),
                timeout=15.0,
            )


def test_retention_tools_eligible_couple_and_duplicate_guard():
    tag = uuid4().hex[:8]
    company_id = f"test-tools-{tag}"
    contact_id = f"couple-eligible-{tag}"
    runtime = SimpleNamespace(
        context=AgentContext(company_id=company_id, job_type="retention")
    )

    database.insert_record(
        "couples",
        {
            "company_id": company_id,
            "contact_id": contact_id,
            "couple_names": "Ava & Elena",
            "email": f"ava.elena.{tag}@example.com",
            "venue": "Hollow Brook Barn",
            "wedding_date": "2025-06-14",
            "marketing_consent": True,
            "do_not_contact": False,
            "has_open_complaint": False,
            "last_outreach_at": (datetime.now(UTC) - timedelta(days=120)).isoformat(),
        },
    )

    history_before = get_couple_history.func(contact_id, runtime=runtime)
    assert "Ava & Elena" in history_before
    assert "Eligibility: ELIGIBLE" in history_before

    draft_result = save_retention_draft.func(
        contact_id=contact_id,
        recipient=f"ava.elena.{tag}@example.com",
        occasion="anniversary",
        subject="Happy Anniversary from Juniper Hollow",
        body="Wishing you both a wonderful first anniversary from all of us at Hollow Brook Barn!",
        runtime=runtime,
    )
    assert "status=pending_review" in draft_result
    assert "requires_review=True" in draft_result

    history_after = get_couple_history.func(contact_id, runtime=runtime)
    assert "existing_pending_retention_draft" in history_after

    with pytest.raises(ValueError, match="existing_pending_retention_draft"):
        save_retention_draft.func(
            contact_id=contact_id,
            recipient=f"ava.elena.{tag}@example.com",
            occasion="anniversary",
            subject="Duplicate Anniversary",
            body="Second draft should be blocked.",
            runtime=runtime,
        )


def test_retention_tools_blocks_ineligible_couple():
    tag = uuid4().hex[:8]
    company_id = f"test-tools-{tag}"
    contact_id = f"couple-blocked-{tag}"
    runtime = SimpleNamespace(
        context=AgentContext(company_id=company_id, job_type="retention")
    )

    database.insert_record(
        "couples",
        {
            "company_id": company_id,
            "contact_id": contact_id,
            "couple_names": "Morgan & Riley",
            "email": f"morgan.riley.{tag}@example.com",
            "venue": "The Juniper Glasshouse",
            "wedding_date": "2025-08-22",
            "marketing_consent": False,
            "do_not_contact": True,
            "has_open_complaint": False,
        },
    )

    history = get_couple_history.func(contact_id, runtime=runtime)
    assert "missing_marketing_consent" in history
    assert "do_not_contact_flag" in history

    with pytest.raises(ValueError, match="missing_marketing_consent"):
        save_retention_draft.func(
            contact_id=contact_id,
            recipient=f"morgan.riley.{tag}@example.com",
            occasion="referral",
            subject="Checking in",
            body="Should not be saved.",
            runtime=runtime,
        )

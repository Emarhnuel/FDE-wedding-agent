from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from src import database
from src.security import auth
from src.security.auth import AuthError, StaffIdentity, authenticate_staff, extract_bearer_token
from src.security.permissions import (
    EMAIL_APPROVER,
    KNOWLEDGE_MANAGER,
    Action,
    allowed_actions,
    authorize,
    has_permission,
    require_company_scope,
    require_permission,
)

DRAFT_ACTIONS = {Action.VIEW_DRAFTS, Action.APPROVE_DRAFT, Action.EDIT_DRAFT, Action.REJECT_DRAFT}
DOCUMENT_ACTIONS = {
    Action.VIEW_DOCUMENTS,
    Action.UPLOAD_DOCUMENT,
    Action.REPLACE_DOCUMENT,
    Action.PUBLISH_DOCUMENT,
    Action.ROLLBACK_DOCUMENT,
}


def make_staff(*roles: str, company_id: str = "juniper-hollow") -> StaffIdentity:
    return StaffIdentity(
        insforge_user_id="user-1",
        staff_id="staff-1",
        company_id=company_id,
        email="staff@example.com",
        full_name="Test Staff",
        roles=frozenset(roles),
    )


# =============================================================================
# permissions.py (pure role logic, no network)
# =============================================================================

def test_email_approver_can_only_review_drafts():
    staff = make_staff(EMAIL_APPROVER)
    assert allowed_actions(staff) == DRAFT_ACTIONS
    for action in DOCUMENT_ACTIONS:
        with pytest.raises(AuthError) as exc:
            require_permission(staff, action)
        assert exc.value.status_code == 403


def test_knowledge_manager_can_only_manage_documents():
    staff = make_staff(KNOWLEDGE_MANAGER)
    assert allowed_actions(staff) == DOCUMENT_ACTIONS
    for action in DRAFT_ACTIONS:
        assert not has_permission(staff, action)


def test_department_head_with_both_roles_gets_both_sets():
    staff = make_staff(EMAIL_APPROVER, KNOWLEDGE_MANAGER)
    assert allowed_actions(staff) == DRAFT_ACTIONS | DOCUMENT_ACTIONS


@pytest.mark.parametrize("roles", [(), ("admin",), ("Email_Approver",), ("project_admin", "authenticated")])
def test_missing_or_unknown_roles_grant_nothing(roles):
    staff = make_staff(*roles)
    assert allowed_actions(staff) == frozenset()
    with pytest.raises(AuthError) as exc:
        require_permission(staff, Action.APPROVE_DRAFT)
    assert exc.value.status_code == 403


def test_company_scope_blocks_other_companies():
    staff = make_staff(EMAIL_APPROVER, company_id="juniper-hollow")
    require_company_scope(staff, "juniper-hollow")
    for company_id in ("other-venue", "", "JUNIPER-HOLLOW"):
        with pytest.raises(AuthError) as exc:
            require_company_scope(staff, company_id)
        assert exc.value.status_code == 403


def test_authorize_requires_both_role_and_company():
    approver = make_staff(EMAIL_APPROVER)
    authorize(approver, Action.APPROVE_DRAFT, company_id="juniper-hollow")

    with pytest.raises(AuthError, match="does not allow"):
        authorize(approver, Action.PUBLISH_DOCUMENT, company_id="juniper-hollow")
    with pytest.raises(AuthError, match="different company"):
        authorize(approver, Action.APPROVE_DRAFT, company_id="other-venue")


# =============================================================================
# auth.py: header parsing and fail-closed behavior (no live session needed)
# =============================================================================

@pytest.mark.parametrize(
    ("header", "token"),
    [("Bearer abc.def.ghi", "abc.def.ghi"), ("bearer   abc ", "abc"), ("  BEARER abc", "abc")],
)
def test_extract_bearer_token_accepts_valid_headers(header, token):
    assert extract_bearer_token(header) == token


@pytest.mark.parametrize("header", [None, "", "   ", "abc", "Bearer", "Bearer ", "Basic abc", "Bearer a b", "Token abc"])
def test_extract_bearer_token_rejects_malformed_headers(header):
    with pytest.raises(AuthError) as exc:
        extract_bearer_token(header)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("token", ["not-a-real-token", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"])
def test_invalid_tokens_are_rejected_by_insforge(token):
    with pytest.raises(AuthError) as exc:
        authenticate_staff(f"Bearer {token}")
    assert exc.value.status_code == 401


def test_admin_api_key_is_not_a_staff_session():
    with pytest.raises(AuthError) as exc:
        authenticate_staff(f"Bearer {database.INSFORGE_API_KEY}")
    assert exc.value.status_code == 401


def test_fails_closed_when_insforge_unreachable(monkeypatch):
    monkeypatch.setattr(database, "INSFORGE_URL", "http://127.0.0.1:9")
    with pytest.raises(AuthError) as exc:
        authenticate_staff("Bearer some-token")
    assert exc.value.status_code == 503


def test_fails_closed_when_insforge_not_configured(monkeypatch):
    monkeypatch.setattr(database, "INSFORGE_URL", "")
    with pytest.raises(AuthError) as exc:
        authenticate_staff("Bearer some-token")
    assert exc.value.status_code == 503


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(404, json={}),
        httpx.Response(200, text="not json"),
    ],
)
def test_fails_closed_on_unexpected_insforge_responses(monkeypatch, response):
    monkeypatch.setattr(auth.httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(AuthError) as exc:
        authenticate_staff("Bearer some-token")
    assert exc.value.status_code == 503


@pytest.mark.parametrize(
    "user",
    [{}, {"email": "x@example.com"}, {"id": "u1", "role": "anon"}, {"id": "u1", "role": "project_admin"}],
)
def test_rejects_sessions_without_an_authenticated_user(monkeypatch, user):
    monkeypatch.setattr(auth.httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"user": user}))
    with pytest.raises(AuthError) as exc:
        authenticate_staff("Bearer some-token")
    assert exc.value.status_code == 401


def test_fails_closed_when_membership_lookup_unavailable(monkeypatch):
    session = httpx.Response(200, json={"user": {"id": "u1", "email": "a@example.com", "role": "authenticated"}})
    monkeypatch.setattr(auth.httpx, "get", lambda *args, **kwargs: session)

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("database down")

    monkeypatch.setattr(database, "query_records", unavailable)
    with pytest.raises(AuthError) as exc:
        authenticate_staff("Bearer some-token")
    assert exc.value.status_code == 503


# =============================================================================
# auth.py + permissions.py end to end against real InsForge accounts
# =============================================================================

def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {database.INSFORGE_API_KEY}", "Content-Type": "application/json"}


@pytest.fixture
def insforge_user():
    """Create a real InsForge account, yield (user_id, access_token, company_id), then delete everything."""
    tag = uuid4().hex[:8]
    company_id = f"test-security-{tag}"
    response = httpx.post(
        f"{database.INSFORGE_URL}/api/auth/users",
        params={"client_type": "server"},
        headers=_admin_headers(),
        json={"email": f"staff.{tag}@juniperhollow-test.com", "password": f"Test-{uuid4().hex}", "name": "Test Staff"},
        timeout=15.0,
    )
    assert response.status_code in (200, 201), f"Could not create test user: {response.status_code} {response.text}"
    body = response.json()
    assert body.get("accessToken"), "Email verification must be off so the test user gets a token immediately"
    user_id = body["user"]["id"]
    try:
        yield user_id, body["accessToken"], company_id
    finally:
        database.run_raw_sql(f"DELETE FROM staff_members WHERE company_id = '{company_id}';")
        httpx.request(
            "DELETE",
            f"{database.INSFORGE_URL}/api/auth/users",
            headers=_admin_headers(),
            json={"userIds": [user_id]},
            timeout=15.0,
        )


def test_valid_login_without_staff_membership_is_forbidden(insforge_user):
    _, token, _ = insforge_user
    with pytest.raises(AuthError) as exc:
        authenticate_staff(f"Bearer {token}")
    assert exc.value.status_code == 403


def test_active_staff_member_is_authenticated_with_database_roles(insforge_user):
    user_id, token, company_id = insforge_user
    staff_row = database.insert_record(
        "staff_members",
        {
            "insforge_user_id": user_id,
            "company_id": company_id,
            "email": "staff@juniperhollow-test.com",
            "full_name": "Jane Approver",
            "roles": [EMAIL_APPROVER, "not_a_real_role"],
        },
    )

    staff = authenticate_staff(f"Bearer {token}")
    assert staff.insforge_user_id == user_id
    assert staff.staff_id == staff_row["id"]
    assert staff.company_id == company_id
    assert staff.full_name == "Jane Approver"
    assert staff.roles == frozenset({EMAIL_APPROVER, "not_a_real_role"})

    # Roles come from the database: the approver can review drafts in their own company only.
    authorize(staff, Action.APPROVE_DRAFT, company_id=company_id)
    with pytest.raises(AuthError) as exc:
        authorize(staff, Action.PUBLISH_DOCUMENT, company_id=company_id)
    assert exc.value.status_code == 403
    with pytest.raises(AuthError) as exc:
        authorize(staff, Action.APPROVE_DRAFT, company_id="juniper-hollow")
    assert exc.value.status_code == 403

    # A promoted department head gains document rights on the next request.
    database.update_records(
        "staff_members", {"roles": [EMAIL_APPROVER, KNOWLEDGE_MANAGER]}, id=f"eq.{staff_row['id']}"
    )
    promoted = authenticate_staff(f"Bearer {token}")
    authorize(promoted, Action.PUBLISH_DOCUMENT, company_id=company_id)
    assert replace(promoted, roles=frozenset()) == replace(staff, roles=frozenset())


def test_deactivated_staff_is_locked_out_even_with_a_valid_token(insforge_user):
    user_id, token, company_id = insforge_user
    staff_row = database.insert_record(
        "staff_members",
        {
            "insforge_user_id": user_id,
            "company_id": company_id,
            "email": "staff@juniperhollow-test.com",
            "roles": [EMAIL_APPROVER],
        },
    )
    assert authenticate_staff(f"Bearer {token}").company_id == company_id

    database.update_records("staff_members", {"is_active": False}, id=f"eq.{staff_row['id']}")
    with pytest.raises(AuthError) as exc:
        authenticate_staff(f"Bearer {token}")
    assert exc.value.status_code == 403

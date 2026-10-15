"""Staff authentication token verification using InsForge session APIs."""

from dataclasses import dataclass
from typing import Any

import httpx

from .. import database

SESSION_PATH = "/api/auth/sessions/current"


class AuthError(Exception):
    """Authentication failure carrying the HTTP status the API should return."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class StaffIdentity:
    """Server-verified staff member: identity from InsForge, membership and roles from our database."""

    insforge_user_id: str
    staff_id: str
    company_id: str
    email: str
    full_name: str | None
    roles: frozenset[str]


def extract_bearer_token(authorization: str | None) -> str:
    """Return the token from an `Authorization: Bearer <token>` header, or raise 401."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise AuthError(401, "Missing or malformed bearer token.")
    return token


def verify_insforge_session(token: str) -> dict[str, Any]:
    """Confirm the token with the fixed InsForge backend and return the session user.

    Fails closed: any InsForge outage, misconfiguration, or unexpected response denies access.
    """
    # Always the configured backend URL, never one derived from the request.
    if not database.INSFORGE_URL:
        raise AuthError(503, "Authentication service is not configured.")
    try:
        response = httpx.get(
            f"{database.INSFORGE_URL}{SESSION_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(503, "Authentication service is unavailable.") from exc

    if response.status_code in (401, 403):
        raise AuthError(401, "Invalid or expired token.")
    if response.status_code != 200:
        raise AuthError(503, "Authentication service returned an unexpected response.")

    try:
        user = response.json().get("user") or {}
    except ValueError as exc:
        raise AuthError(503, "Authentication service returned an unexpected response.") from exc
    # Live responses omit "role"; reject only an explicit non-user role (e.g. anon) or a missing ID.
    if not user.get("id") or user.get("role", "authenticated") != "authenticated":
        raise AuthError(401, "Token does not belong to an authenticated user.")
    return user


def resolve_staff_membership(insforge_user_id: str) -> dict[str, Any]:
    """Load the active, company-scoped staff record for a verified InsForge user, or raise 403."""
    try:
        rows = database.query_records(
            "staff_members",
            insforge_user_id=f"eq.{insforge_user_id}",
            is_active="is.true",
            limit=1,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        raise AuthError(503, "Staff membership lookup is unavailable.") from exc
    if not rows:
        # Covers unknown users and deactivated staff whose tokens have not yet expired.
        raise AuthError(403, "No active staff membership for this account.")
    return rows[0]


def authenticate_staff(authorization: str | None) -> StaffIdentity:
    """Authenticate a staff API request from its Authorization header.

    Raises AuthError: 401 for a missing, malformed, invalid, or expired token; 403 when the
    identity has no active staff membership; 503 when InsForge cannot be reached.
    Roles and company always come from our staff_members table, never from the token or request.
    """
    user = verify_insforge_session(extract_bearer_token(authorization))
    staff = resolve_staff_membership(str(user["id"]))
    roles = staff.get("roles") or []
    return StaffIdentity(
        insforge_user_id=str(user["id"]),
        staff_id=str(staff["id"]),
        company_id=str(staff["company_id"]),
        email=str(user.get("email") or staff.get("email") or ""),
        full_name=staff.get("full_name"),
        roles=frozenset(role for role in roles if isinstance(role, str)) if isinstance(roles, list) else frozenset(),
    )

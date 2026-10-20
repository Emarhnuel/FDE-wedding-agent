"""Staff authentication token verification using InsForge session APIs."""

from dataclasses import dataclass
from typing import Any

import httpx

from .. import database

SESSION_PATH = "/api/auth/sessions/current"
SIGN_IN_PATH = "/api/auth/sessions"
USERS_PATH = "/api/auth/users"
SEND_RESET_CODE_PATH = "/api/auth/email/send-reset-password"
EXCHANGE_RESET_CODE_PATH = "/api/auth/email/exchange-reset-password-token"
RESET_PASSWORD_PATH = "/api/auth/email/reset-password"


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


def sign_in(email: str, password: str) -> str:
    """Exchange an email and password for an InsForge access token, or raise AuthError.

    This proves identity only; staff membership and roles are checked on every protected request.
    """
    if not database.INSFORGE_URL:
        raise AuthError(503, "Authentication service is not configured.")
    try:
        response = httpx.post(
            f"{database.INSFORGE_URL}{SIGN_IN_PATH}",
            params={"client_type": "server"},
            json={"method": "password", "email": email, "password": password},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(503, "Authentication service is unavailable.") from exc
    if response.status_code in (400, 401, 403, 404):
        raise AuthError(401, "Invalid email or password.")
    try:
        token = response.json().get("accessToken") if response.status_code == 200 else None
    except ValueError:
        token = None
    if not token:
        raise AuthError(503, "Authentication service returned an unexpected response.")
    return token


def insforge_message(response: httpx.Response, default: str) -> str:
    """InsForge's own error message (e.g. a password rule), or the default."""
    try:
        return str(response.json().get("message") or default)
    except ValueError:
        return default


def create_login(email: str, name: str, password: str) -> str:
    """Create an InsForge login with the password the staff admin chose; returns its user ID.

    Raises AuthError 409 if the email exists, 422 if InsForge rejects the password. Uses the
    server-side admin key, never a browser.
    """
    try:
        response = httpx.post(
            f"{database.INSFORGE_URL}{USERS_PATH}",
            params={"client_type": "server"},
            headers={"Authorization": f"Bearer {database.INSFORGE_API_KEY}"},
            json={"email": email, "password": password, "name": name},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(503, "Authentication service is unavailable.") from exc
    if response.status_code == 409:
        raise AuthError(409, "A login with this email already exists.")
    if response.status_code == 400:
        raise AuthError(422, insforge_message(response, "The password does not meet the requirements."))
    if response.status_code not in (200, 201):
        raise AuthError(503, "Authentication service returned an unexpected response.")
    return str(response.json()["user"]["id"])


def send_password_code(email: str) -> None:
    """Ask InsForge to email a 6-digit password reset code. InsForge never reveals if the email exists."""
    try:
        response = httpx.post(f"{database.INSFORGE_URL}{SEND_RESET_CODE_PATH}", json={"email": email}, timeout=15.0)
    except httpx.HTTPError as exc:
        raise AuthError(503, "Authentication service is unavailable.") from exc
    if response.status_code >= 500:
        raise AuthError(503, "Authentication service returned an unexpected response.")


def reset_password(email: str, code: str, new_password: str) -> None:
    """Exchange the emailed code for a reset token, then set the new password (InsForge code method)."""
    try:
        exchange = httpx.post(
            f"{database.INSFORGE_URL}{EXCHANGE_RESET_CODE_PATH}", json={"email": email, "code": code}, timeout=15.0
        )
        if 400 <= exchange.status_code < 500:
            raise AuthError(400, "That code is invalid or has expired.")
        token = exchange.json().get("token") if exchange.is_success else None
        if not token:
            raise AuthError(503, "Authentication service returned an unexpected response.")
        reset = httpx.post(
            f"{database.INSFORGE_URL}{RESET_PASSWORD_PATH}", json={"newPassword": new_password, "otp": token}, timeout=15.0
        )
    except httpx.HTTPError as exc:
        raise AuthError(503, "Authentication service is unavailable.") from exc
    except ValueError as exc:
        raise AuthError(503, "Authentication service returned an unexpected response.") from exc
    if 400 <= reset.status_code < 500:
        raise AuthError(400, insforge_message(reset, "The new password was not accepted."))
    if not reset.is_success:
        raise AuthError(503, "Authentication service returned an unexpected response.")


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

"""Role-based authorization checks for email review and knowledge management."""

from enum import StrEnum

from .auth import AuthError, StaffIdentity

EMAIL_APPROVER = "email_approver"
KNOWLEDGE_MANAGER = "knowledge_manager"


class Action(StrEnum):
    """Staff actions the API authorizes."""

    VIEW_DRAFTS = "view_drafts"
    APPROVE_DRAFT = "approve_draft"
    EDIT_DRAFT = "edit_draft"
    REJECT_DRAFT = "reject_draft"
    VIEW_DOCUMENTS = "view_documents"
    UPLOAD_DOCUMENT = "upload_document"
    REPLACE_DOCUMENT = "replace_document"
    PUBLISH_DOCUMENT = "publish_document"
    ROLLBACK_DOCUMENT = "rollback_document"


# The two roles are independent: holding one never implies the other. Unknown roles grant nothing.
ROLE_PERMISSIONS: dict[str, frozenset[Action]] = {
    EMAIL_APPROVER: frozenset(
        {Action.VIEW_DRAFTS, Action.APPROVE_DRAFT, Action.EDIT_DRAFT, Action.REJECT_DRAFT}
    ),
    KNOWLEDGE_MANAGER: frozenset(
        {
            Action.VIEW_DOCUMENTS,
            Action.UPLOAD_DOCUMENT,
            Action.REPLACE_DOCUMENT,
            Action.PUBLISH_DOCUMENT,
            Action.ROLLBACK_DOCUMENT,
        }
    ),
}


def allowed_actions(staff: StaffIdentity) -> frozenset[Action]:
    """Return every action granted by the staff member's roles."""
    return frozenset().union(*(ROLE_PERMISSIONS.get(role, frozenset()) for role in staff.roles))


def has_permission(staff: StaffIdentity, action: Action) -> bool:
    """Return True when one of the staff member's roles grants the action."""
    return action in allowed_actions(staff)


def require_permission(staff: StaffIdentity, action: Action) -> None:
    """Raise 403 unless the staff member's roles grant the action."""
    if not has_permission(staff, action):
        raise AuthError(403, f"Your role does not allow '{action}'.")


def require_company_scope(staff: StaffIdentity, company_id: str) -> None:
    """Raise 403 unless the target record belongs to the staff member's company."""
    if not company_id or company_id != staff.company_id:
        raise AuthError(403, "This record belongs to a different company.")


def authorize(staff: StaffIdentity, action: Action, *, company_id: str) -> None:
    """Check role and company scope before any side effect; raises 403 on either failure.

    company_id must come from the stored target record (draft, document), never the request.
    """
    require_permission(staff, action)
    require_company_scope(staff, company_id)

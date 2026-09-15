"""Session and Refresh Token Family tracking with automated reuse detection."""

import time
import logging
from typing import Dict, Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# In-memory store for active session families with thread-safety
# For multi-node scaling, this can seamlessly sync with DynamoDB TTL items.
_active_families: Dict[str, Dict[str, Any]] = {}

def create_session_family(user_id: str, tenant_id: str) -> tuple[str, str]:
    """
    Create a new refresh token family for a login session.
    Returns: (family_id, token_id)
    """
    family_id = f"fam_{uuid4().hex}"
    token_id = f"tok_{uuid4().hex}"
    now = time.time()
    
    _active_families[family_id] = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "current_token_id": token_id,
        "created_at": now,
        "last_rotated_at": now,
        "is_revoked": False,
    }
    return family_id, token_id

def rotate_refresh_token(family_id: str, token_id: str) -> Optional[tuple[str, str, str]]:
    """
    Validate and rotate refresh token within its family.
    Returns (user_id, tenant_id, new_token_id) on success.
    Returns None if token reuse or revoked family detected (and revokes entire family).
    """
    family = _active_families.get(family_id)
    if not family:
        logger.warning("[AUTH] Refresh token validation failed: family %s not found", family_id)
        return None
        
    if family.get("is_revoked"):
        logger.warning("[AUTH] Attempted use of revoked refresh token family %s", family_id)
        return None
        
    current_token_id = family.get("current_token_id")
    if token_id != current_token_id:
        # REUSE DETECTED! Revoke entire family immediately
        family["is_revoked"] = True
        logger.critical(
            "[AUTH] REFRESH TOKEN REUSE DETECTED for user %s on family %s! Revoking all sessions in family.",
            family.get("user_id"),
            family_id,
        )
        return None
        
    # Rotate token_id
    new_token_id = f"tok_{uuid4().hex}"
    family["current_token_id"] = new_token_id
    family["last_rotated_at"] = time.time()
    
    return family["user_id"], family["tenant_id"], new_token_id

def revoke_session_family(family_id: str) -> None:
    """Revoke a refresh token family upon user logout."""
    if family_id in _active_families:
        _active_families[family_id]["is_revoked"] = True
        del _active_families[family_id]

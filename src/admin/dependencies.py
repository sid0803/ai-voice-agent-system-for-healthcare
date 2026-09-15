"""Authentication, RBAC Permission checking, and Multi-Tenant resolution dependencies."""

import time
import secrets
import logging
from typing import Dict, Any, List, Set, Optional, Callable

import jwt
from fastapi import Request, HTTPException, Depends, status

from src.admin.config import (
    ADMIN_JWT_SECRET,
    JWT_ALGORITHM,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    REFRESH_TOKEN_EXPIRE_SECONDS,
    COOKIE_ACCESS_NAME,
    COOKIE_REFRESH_NAME,
    COOKIE_CSRF_NAME,
    ROLE_PERMISSIONS,
    ALL_PERMISSIONS,
)
from src.admin.audit import audit_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token Utilities
# ---------------------------------------------------------------------------
def generate_csrf_token() -> str:
    """Generate a high-entropy CSRF defense token."""
    return secrets.token_urlsafe(32)

def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str,
    permissions: Optional[List[str]] = None,
    expires_in: int = ACCESS_TOKEN_EXPIRE_SECONDS,
) -> str:
    """Create a signed short-lived JWT access token."""
    now = int(time.time())
    if permissions is None:
        permissions = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["staff"])
        
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "permissions": permissions,
        "iat": now,
        "exp": now + expires_in,
        "type": "access",
    }
    return jwt.encode(payload, ADMIN_JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(
    user_id: str,
    tenant_id: str,
    family_id: str,
    token_id: str,
    expires_in: int = REFRESH_TOKEN_EXPIRE_SECONDS,
) -> str:
    """Create a signed long-lived JWT refresh token with family tracking."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "family_id": family_id,
        "token_id": token_id,
        "iat": now,
        "exp": now + expires_in,
        "type": "refresh",
    }
    return jwt.encode(payload, ADMIN_JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate signature and expiry of a JWT."""
    try:
        return jwt.decode(token, ADMIN_JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
        )

def get_client_ip(request: Request) -> str:
    """Resolve client IP, honoring reverse proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


# ---------------------------------------------------------------------------
# FastAPI Dependency Callables
# ---------------------------------------------------------------------------
async def get_current_user(request: Request) -> Dict[str, Any]:
    """
    Extract and validate authenticated user from HttpOnly cookie or Authorization header.
    Returns: {"user_id": str, "tenant_id": str, "role": str, "permissions": Set[str]}
    """
    token = request.cookies.get(COOKIE_ACCESS_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
        
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
        
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role", "staff")
    permissions = set(payload.get("permissions", []))
    
    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
        )
        
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "permissions": permissions,
    }


def require_permission(permission: str) -> Callable:
    """
    Higher-order dependency to enforce granular RBAC permissions.
    Automatically logs PERMISSION_DENIED to the audit trail if unauthorized.
    """
    async def permission_checker(
        request: Request,
        user: Dict[str, Any] = Depends(get_current_user),
    ) -> Dict[str, Any]:
        if permission not in user["permissions"]:
            client_ip = get_client_ip(request)
            audit_service.log(
                tenant_id=user["tenant_id"],
                user_id=user["user_id"],
                user_role=user["role"],
                action="PERMISSION_DENIED",
                resource_type="RBAC",
                resource_id=permission,
                status="DENIED",
                ip_address=client_ip,
                user_agent=request.headers.get("user-agent", ""),
                metadata={"required_permission": permission, "path": str(request.url.path)},
            )
            logger.warning(
                "[RBAC] Access denied for user %s on tenant %s: missing %s",
                user["user_id"],
                user["tenant_id"],
                permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: insufficient permissions (requires '{permission}')",
            )
        return user

    return permission_checker


async def verify_csrf_token(request: Request) -> None:
    """
    Validate real X-CSRF-Token header against HttpOnly CSRF cookie on mutating methods.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        cookie_csrf = request.cookies.get(COOKIE_CSRF_NAME)
        header_csrf = request.headers.get("X-CSRF-Token")
        
        # If accessing via pure Bearer token header, skip cookie CSRF (API client mode)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and not request.cookies.get(COOKIE_ACCESS_NAME):
            return
            
        if not cookie_csrf or not header_csrf or not secrets.compare_digest(cookie_csrf, header_csrf):
            logger.warning("[CSRF] CSRF token validation failed for path %s", request.url.path)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF verification failed or token missing",
            )

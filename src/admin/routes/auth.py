"""Authentication routes: Login, Logout, Me, Refresh, and Staff Invitations."""

import os
import time
import bcrypt
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from fastapi import APIRouter, Request, Response, HTTPException, Depends, status
from fastapi.responses import JSONResponse

from src.admin.config import (
    IS_PRODUCTION,
    COOKIE_ACCESS_NAME,
    COOKIE_REFRESH_NAME,
    COOKIE_CSRF_NAME,
    COOKIE_SECURE,
    COOKIE_SAMESITE,
    ACCESS_TOKEN_EXPIRE_SECONDS,
    REFRESH_TOKEN_EXPIRE_SECONDS,
    ROLE_PERMISSIONS,
)
from src.admin.dependencies import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_csrf_token,
    get_current_user,
    require_permission,
    verify_csrf_token,
    get_client_ip,
)
from src.admin.session_store import (
    create_session_family,
    rotate_refresh_token,
    revoke_session_family,
)
from src.admin.audit import audit_service
from src.analytics.dynamodb_client import dynamodb_analytics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Admin Auth"])

# Rate limit tracking (Brute-force defense: 5 failed attempts per 15 mins)
_login_failures: Dict[str, List[float]] = {}

def _check_rate_limit(key: str) -> None:
    now = time.time()
    window = 15 * 60
    failures = [t for t in _login_failures.get(key, []) if (now - t) < window]
    if failures:
        _login_failures[key] = failures
    else:
        _login_failures.pop(key, None)

    # Bounded cleanup: prevent memory exhaustion from random IP scans
    if len(_login_failures) > 500:
        expired_keys = [k for k, v in _login_failures.items() if not v or (now - v[-1]) >= window]
        for k in expired_keys:
            _login_failures.pop(k, None)

    if len(failures) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again in 15 minutes.",
        )

def _record_failure(key: str) -> None:
    now = time.time()
    if key not in _login_failures:
        _login_failures[key] = []
    _login_failures[key].append(now)



# Request Schemas
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=4, max_length=128)

class InviteRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field("staff", pattern="^(hospital_admin|doctor|staff|receptionist)$")
    hospital_id: Optional[str] = None


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    """Authenticate admin or staff user and set secure HttpOnly session cookies."""
    client_ip = get_client_ip(request)
    rate_key = f"{client_ip}:{req.username.lower()}"
    _check_rate_limit(rate_key)

    user = dynamodb_analytics.get_user(req.username)
    if not user:
        # Check local staging evaluation fallback - strictly prohibited in production
        allow_dev_passwords = os.environ.get("ALLOW_DEV_PASSWORDS", "false").lower() == "true"
        password_valid = False

        if not IS_PRODUCTION and allow_dev_passwords:
            staging_defaults = {
                "admin": {"hospital_id": "apollo_metro", "role": "hospital_admin", "pw": "admin123"},
                "dr_amit": {"hospital_id": "apollo_metro", "role": "doctor", "pw": "doctor123"},
                "reception_1": {"hospital_id": "apollo_metro", "role": "receptionist", "pw": "staff123"},
            }
            fallback = staging_defaults.get(req.username.lower())
            if fallback and req.password == fallback["pw"]:
                logger.warning(
                    "[SECURITY WARNING] Staging development password accepted for user '%s'. "
                    "This bypass is strictly disabled in production.", req.username
                )
                user = {
                    "username": req.username,
                    "hospital_id": fallback["hospital_id"],
                    "role": fallback["role"],
                    "password_hash": "staging_verified",
                }
                password_valid = True

        if not user or not password_valid:
            _record_failure(rate_key)
            audit_service.log(
                tenant_id="UNKNOWN",
                user_id=req.username,
                user_role="guest",
                action="LOGIN_FAILED",
                resource_type="AUTH",
                status="FAILED",
                ip_address=client_ip,
                user_agent=request.headers.get("user-agent", ""),
                metadata={"reason": "user_not_found"},
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    else:
        stored_hash = user.get("password_hash", "")
        password_valid = False
        try:
            if stored_hash and bcrypt.checkpw(req.password.encode("utf-8"), stored_hash.encode("utf-8")):
                password_valid = True
        except Exception:
            password_valid = False

    if not password_valid:
        _record_failure(rate_key)
        audit_service.log(
            tenant_id=user.get("hospital_id", "UNKNOWN"),
            user_id=req.username,
            user_role=user.get("role", "staff"),
            action="LOGIN_FAILED",
            resource_type="AUTH",
            status="FAILED",
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", ""),
            metadata={"reason": "invalid_password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Clear rate limiter on successful authentication
    _login_failures.pop(rate_key, None)

    tenant_id = user.get("hospital_id", "apollo_metro")
    role = user.get("role", "staff")
    permissions = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["staff"])

    # Create token family and tokens
    family_id, token_id = create_session_family(user_id=req.username, tenant_id=tenant_id)
    access_token = create_access_token(
        user_id=req.username,
        tenant_id=tenant_id,
        role=role,
        permissions=permissions,
    )
    refresh_token = create_refresh_token(
        user_id=req.username,
        tenant_id=tenant_id,
        family_id=family_id,
        token_id=token_id,
    )
    csrf_token = generate_csrf_token()

    # Set secure HttpOnly cookies
    response.set_cookie(
        key=COOKIE_ACCESS_NAME,
        value=access_token,
        max_age=ACCESS_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key=COOKIE_REFRESH_NAME,
        value=refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/api/v1/auth",
    )
    # CSRF cookie is non-HttpOnly so client JS can read and send in X-CSRF-Token header
    response.set_cookie(
        key=COOKIE_CSRF_NAME,
        value=csrf_token,
        max_age=ACCESS_TOKEN_EXPIRE_SECONDS,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )

    audit_service.log(
        tenant_id=tenant_id,
        user_id=req.username,
        user_role=role,
        action="LOGIN_SUCCESS",
        resource_type="AUTH",
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    return {
        "status": "ok",
        "user": {
            "username": req.username,
            "tenant_id": tenant_id,
            "role": role,
            "permissions": permissions,
        },
        "csrf_token": csrf_token,
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Revoke session family and clear auth cookies."""
    client_ip = get_client_ip(request)
    refresh_token = request.cookies.get(COOKIE_REFRESH_NAME)
    if refresh_token:
        try:
            payload = decode_token(refresh_token)
            family_id = payload.get("family_id")
            if family_id:
                revoke_session_family(family_id)
        except Exception:
            pass

    response.delete_cookie(COOKIE_ACCESS_NAME, path="/")
    response.delete_cookie(COOKIE_REFRESH_NAME, path="/api/v1/auth")
    response.delete_cookie(COOKIE_CSRF_NAME, path="/")

    return {"status": "ok", "message": "Logged out successfully"}


@router.get("/me")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    """Fetch current user profile and permission list."""
    tenant = dynamodb_analytics.get_tenant(user["tenant_id"]) or {}
    return {
        "user": {
            "username": user["user_id"],
            "tenant_id": user["tenant_id"],
            "tenant_name": tenant.get("hospital_name") or tenant.get("name") or "SarvoDaya Hospital",
            "role": user["role"],
            "permissions": list(user["permissions"]),
        }
    }


@router.post("/refresh")
async def refresh_tokens(request: Request, response: Response):
    """Rotate access and refresh tokens with automated reuse detection."""
    client_ip = get_client_ip(request)
    refresh_token = request.cookies.get(COOKIE_REFRESH_NAME)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    family_id = payload.get("family_id")
    token_id = payload.get("token_id")
    if not family_id or not token_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed refresh token")

    rotation_result = rotate_refresh_token(family_id, token_id)
    if not rotation_result:
        # REUSE OR INVALID FAMILY! Invalidate cookies immediately
        response.delete_cookie(COOKIE_ACCESS_NAME, path="/")
        response.delete_cookie(COOKIE_REFRESH_NAME, path="/api/v1/auth")
        response.delete_cookie(COOKIE_CSRF_NAME, path="/")

        audit_service.log(
            tenant_id=payload.get("tenant_id", "UNKNOWN"),
            user_id=payload.get("sub", "UNKNOWN"),
            user_role="unknown",
            action="TOKEN_REUSE_DETECTED",
            resource_type="AUTH",
            status="DENIED",
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent", ""),
            metadata={"family_id": family_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security violation: token reuse detected. Please log in again.",
        )

    user_id, tenant_id, new_token_id = rotation_result
    user_record = dynamodb_analytics.get_user(user_id) or {}
    role = user_record.get("role", "staff")
    permissions = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["staff"])

    new_access_token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        permissions=permissions,
    )
    new_refresh_token = create_refresh_token(
        user_id=user_id,
        tenant_id=tenant_id,
        family_id=family_id,
        token_id=new_token_id,
    )
    csrf_token = generate_csrf_token()

    response.set_cookie(
        key=COOKIE_ACCESS_NAME,
        value=new_access_token,
        max_age=ACCESS_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key=COOKIE_REFRESH_NAME,
        value=new_refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/api/v1/auth",
    )
    response.set_cookie(
        key=COOKIE_CSRF_NAME,
        value=csrf_token,
        max_age=ACCESS_TOKEN_EXPIRE_SECONDS,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )

    return {"status": "ok", "csrf_token": csrf_token}


@router.post("/invite", dependencies=[Depends(require_permission("users.manage")), Depends(verify_csrf_token)])
async def invite_user(
    req: InviteRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Invite and onboard new staff or doctor for the authenticated tenant."""
    client_ip = get_client_ip(request)

    # Multi-tenant isolation: Hospital admin can only invite users to their OWN hospital
    target_tenant = current_user["tenant_id"]
    if current_user["role"] == "super_admin" and req.hospital_id:
        target_tenant = req.hospital_id

    # Check if username already exists
    if dynamodb_analytics.get_user(req.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{req.username}' already exists",
        )

    # Salt & Hash password
    salt = bcrypt.gensalt(rounds=12)
    pw_hash = bcrypt.hashpw(req.password.encode("utf-8"), salt).decode("utf-8")

    success = dynamodb_analytics.save_user(
        username=req.username,
        password_hash=pw_hash,
        hospital_id=target_tenant,
        role=req.role,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist user in DynamoDB",
        )

    audit_service.log(
        tenant_id=target_tenant,
        user_id=current_user["user_id"],
        user_role=current_user["role"],
        action="USER_INVITED",
        resource_type="USER",
        resource_id=req.username,
        status="SUCCESS",
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        after_state={"username": req.username, "role": req.role, "tenant_id": target_tenant},
    )

    return {
        "status": "created",
        "user": {
            "username": req.username,
            "tenant_id": target_tenant,
            "role": req.role,
        },
    }

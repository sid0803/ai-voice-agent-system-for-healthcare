"""Configuration constants and RBAC permission mappings for the Admin Portal."""

import os
from typing import Dict, List, Set

IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development").lower() == "production"

# JWT & Cookie configuration
_raw_jwt_secret = os.environ.get("ADMIN_JWT_SECRET")
if IS_PRODUCTION:
    if not _raw_jwt_secret:
        raise RuntimeError(
            "[SECURITY CRITICAL] ADMIN_JWT_SECRET environment variable is required in production mode. Refusing to start with insecure fallback."
        )
    if len(_raw_jwt_secret) < 32 or _raw_jwt_secret in (
        "super-secret-jwt-key-for-admin-portal-change-in-production",
        "dev_jwt_secret_for_local_testing_only_not_for_production",
        "replace_with_a_secure_jwt_secret_min_32_chars",
    ):
        raise RuntimeError(
            "[SECURITY CRITICAL] Insecure or weak ADMIN_JWT_SECRET detected in production. Secret must be at least 32 characters and non-default."
        )
ADMIN_JWT_SECRET = _raw_jwt_secret or "dev_jwt_secret_for_local_testing_only_not_for_production"

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_SECONDS = int(os.environ.get("ACCESS_TOKEN_EXPIRE_SECONDS", 15 * 60))  # 15 mins
REFRESH_TOKEN_EXPIRE_SECONDS = int(os.environ.get("REFRESH_TOKEN_EXPIRE_SECONDS", 7 * 24 * 3600))  # 7 days

COOKIE_ACCESS_NAME = "indiiserve_access_token"
COOKIE_REFRESH_NAME = "indiiserve_refresh_token"
COOKIE_CSRF_NAME = "indiiserve_csrf_token"

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false" if not IS_PRODUCTION else "true").lower() == "true"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax")  # 'lax' or 'strict'

# Outbound Telephony Kill-Switch
OUTBOUND_CALLING_ENABLED = os.environ.get("OUTBOUND_CALLING_ENABLED", "false").lower() == "true"

# Atomic Permissions definition
ALL_PERMISSIONS: Set[str] = {
    "calls.read",
    "calls.read_sensitive",
    "appointments.read",
    "appointments.write",
    "appointments.cancel",
    "triage.read",
    "triage.acknowledge",
    "triage.resolve",
    "knowledge.read",
    "knowledge.write_draft",
    "knowledge.publish",
    "facts.review",
    "facts.approve",
    "facts.reject",
    "telephony.single_outbound",
    "users.manage",
    "audit.read",
    "sandbox.simulate",
    "analytics.read",
}

# Role-to-Permissions Mapping
ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "super_admin": list(ALL_PERMISSIONS),
    "hospital_admin": [
        "calls.read",
        "calls.read_sensitive",
        "appointments.read",
        "appointments.write",
        "appointments.cancel",
        "triage.read",
        "triage.acknowledge",
        "triage.resolve",
        "knowledge.read",
        "knowledge.write_draft",
        "knowledge.publish",
        "facts.review",
        "facts.approve",
        "facts.reject",
        "telephony.single_outbound",
        "users.manage",
        "audit.read",
        "sandbox.simulate",
        "analytics.read",
    ],
    "doctor": [
        "calls.read",
        "appointments.read",
        "triage.read",
        "triage.acknowledge",
        "triage.resolve",
        "knowledge.read",
        "sandbox.simulate",
        "analytics.read",
    ],
    "staff": [
        "calls.read",
        "appointments.read",
        "appointments.write",
        "appointments.cancel",
        "triage.read",
        "triage.acknowledge",
        "knowledge.read",
        "sandbox.simulate",
        "analytics.read",
    ],
    "receptionist": [
        "calls.read",
        "appointments.read",
        "appointments.write",
        "appointments.cancel",
        "triage.read",
        "triage.acknowledge",
        "knowledge.read",
        "sandbox.simulate",
        "analytics.read",
    ],
}


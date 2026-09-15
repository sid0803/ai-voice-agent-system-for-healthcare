import hmac
import pytest
from src.server import mask_phone, detect_language

def test_mask_phone():
    """Verify phone masking handles various formats without exposing PII."""
    assert mask_phone("9876543210") == "987******3210"
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("") == "unknown"
    assert mask_phone("123") == "123"

def test_hmac_constant_time_digest():
    """Verify constant time comparison works as expected."""
    token_a = "secret_push_token_xyz"
    token_b = "secret_push_token_xyz"
    token_c = "wrong_push_token_123"
    assert hmac.compare_digest(token_a, token_b) is True
    assert hmac.compare_digest(token_a, token_c) is False

def test_language_detection():
    """Verify language detector handles Hindi, Hinglish, and English."""
    assert detect_language("Hello, I need an appointment with a doctor") == "english"
    assert detect_language("मुझे डॉक्टर से मिलना है") == "hindi"
    assert detect_language("Doctor kab milenge?") == "hinglish"


# ---------------------------------------------------------------------------
# Phase A Security Regression Tests
# ---------------------------------------------------------------------------

def test_no_hardcoded_exotel_credentials_in_check_exotel():
    """Verify scratch/check_exotel.py uses os.environ and has no hardcoded secrets."""
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_file = os.path.join(repo_root, "scratch", "check_exotel.py")
    with open(target_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'os.environ.get("EXOTEL_API_KEY")' in content
    assert 'os.environ.get("EXOTEL_API_TOKEN")' in content
    assert "d341b12bf96f67d419047f72e7d0fdd142d3e80b2ecc7236" not in content
    assert "c8a271d43bd6878fb25b2d7a8641416b75d466cb24692280" not in content


def test_admin_password_hash_no_hardcoded_fallback_in_server():
    """Verify src/server.py does not contain hardcoded default bcrypt hash fallbacks."""
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_file = os.path.join(repo_root, "src", "server.py")
    with open(server_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$2b$12$yR/MslXD5e/A/UH1oLLU6eFPCoe6MkhOekURMmeaqezJVHvnR5Gtu" not in content
    assert 'ADMIN_PASSWORD_HASH is required in production' in content


def test_admin_jwt_secret_required_in_production(monkeypatch):
    """Verify that ADMIN_JWT_SECRET raises RuntimeError in production when missing."""
    import os
    import importlib
    import src.admin.config as admin_config
    
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
    
    with pytest.raises(RuntimeError) as exc_info:
        importlib.reload(admin_config)
    assert "ADMIN_JWT_SECRET environment variable is required in production mode" in str(exc_info.value)
    
    # Restore dev mode
    monkeypatch.setenv("ENVIRONMENT", "development")
    importlib.reload(admin_config)


def test_admin_jwt_secret_weak_rejected_in_production(monkeypatch):
    """Verify that weak or default ADMIN_JWT_SECRET raises RuntimeError in production."""
    import importlib
    import src.admin.config as admin_config
    
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ADMIN_JWT_SECRET", "short_secret")
    
    with pytest.raises(RuntimeError) as exc_info:
        importlib.reload(admin_config)
    assert "Insecure or weak ADMIN_JWT_SECRET detected" in str(exc_info.value)
    
    # Restore dev mode
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
    importlib.reload(admin_config)



def test_frontend_source_no_staging_credentials():
    """Verify portal/src/views/LoginView.jsx initializes fields empty without staging presets."""
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    login_view_file = os.path.join(repo_root, "portal", "src", "views", "LoginView.jsx")
    with open(login_view_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "useState('')" in content
    assert "admin123" not in content
    assert "doctor123" not in content
    assert "staff123" not in content
    assert "Quick Role Switcher" not in content


def test_gitignore_protects_credentials_and_keys():
    """Verify .gitignore includes rules for private keys, certs, GCP credentials, and env files."""
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gitignore_file = os.path.join(repo_root, ".gitignore")
    with open(gitignore_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "*.pem" in content
    assert "*.key" in content
    assert "credentials.json" in content
    assert ".env.*" in content
    assert "!.env.example" in content
    assert "portal/dist/" in content


def test_portal_dist_bundle_no_staging_credentials():
    """Verify portal/dist assets do not leak staging credentials."""
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dist_dir = os.path.join(repo_root, "portal", "dist")
    if not os.path.exists(dist_dir):
        pytest.skip("portal/dist not present")
        
    for root, _, files in os.walk(dist_dir):
        for f in files:
            if f.endswith((".js", ".html", ".css")):
                with open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore") as fh:
                    c = fh.read()
                assert "admin123" not in c
                assert "doctor123" not in c
                assert "staff123" not in c


def test_staging_passwords_blocked_by_default():
    """Verify staging default passwords (admin123, doctor123) are rejected when ALLOW_DEV_PASSWORDS is not set."""
    from fastapi.testclient import TestClient
    from src.server import app
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 401
    assert "Invalid credentials" in resp.json().get("detail", "")


"""Final deployment readiness scanner."""
import ast, os, sys

print("=" * 55)
print("  FINAL DEPLOYMENT READINESS SCAN")
print("=" * 55)

# ---- 1. Syntax check ----
print()
print("[1] SYNTAX CHECK (19 files)")
files = [
    "src/server.py", "src/nova_client.py", "src/tools.py",
    "src/mock_engine.py", "src/integrations/sync_engine.py",
    "src/learning/distiller.py", "src/integrations/sheets_client.py",
    "src/analytics/rds_client.py", "src/analytics/processor.py",
    "src/memory_manager.py", "src/transcript_store.py",
    "src/routing/intent_router.py", "src/cache/response_cache.py",
    "src/integrations/tenant_manager.py", "src/credential_validation.py",
    "src/audio_utils.py", "src/security/audit_logger.py",
    "src/diagnostics/health.py", "src/integrations/local_sink.py",
]
syntax_errors = 0
for f in files:
    if not os.path.exists(f):
        print(f"  MISSING  {f}")
        syntax_errors += 1
        continue
    try:
        ast.parse(open(f, encoding="utf-8").read())
    except SyntaxError as e:
        print(f"  SYNTAX_ERR  {f}: line {e.lineno}: {e.msg}")
        syntax_errors += 1
if syntax_errors == 0:
    print(f"  PASS: All {len(files)} files clean")

# ---- 2. Code fix verification ----
print()
print("[2] CODE FIX VERIFICATION")

rds = open("src/analytics/rds_client.py", encoding="utf-8").read()
nc  = open("src/nova_client.py",           encoding="utf-8").read()
srv = open("src/server.py",                encoding="utf-8").read()
tm  = open("src/integrations/tenant_manager.py", encoding="utf-8").read()
mm  = open("src/memory_manager.py",        encoding="utf-8").read()
tools = open("src/tools.py",               encoding="utf-8").read()

checks = [
    ("D-03 Fernet guard in rds_client",       "Invalid ENCRYPTION_KEY" in rds),
    ("D-04/D-16 no bare basicConfig in nova", "logging.basicConfig(level=logging.DEBUG)" not in nc),
    ("D-05 EXOTEL_API_BASE None-guard",       'EXOTEL_API_BASE = ""' in srv),
    ("D-06 setup_prompt_start() added",       "await session.setup_prompt_start()" in srv),
    ("D-07 _stream_ready.set() in mock mode", "session._stream_ready.set()" in nc),
    ("D-08 status:live in hardcoded fallback", '"status": "live"' in tm),
    ("D-09 audit_logger consistency",         "audit_logger." not in tools or "import audit_logger" in tools),
    ("D-10 asyncio.to_thread for create_event", "asyncio.to_thread" in mm),
    ("D-12 stream_sid guard in audio output", "if not session.stream_sid:" in srv),
    ("D-18 log level INFO not DEBUG",         "level=logging.INFO" in srv),
]

all_code_ok = True
for name, ok in checks:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_code_ok = False
    print(f"  {status:4}  {name}")

# ---- 3. Assets ----
print()
print("[3] PCM ASSETS")
assets_ok = True
for asset in ["assets/hello.pcm", "assets/transfer.pcm", "assets/emergency.pcm"]:
    if os.path.exists(asset):
        size = os.path.getsize(asset)
        print(f"  PASS     {asset} ({size:,} bytes)")
    else:
        print(f"  MISSING  {asset}")
        assets_ok = False

# ---- 4. .env config ----
print()
print("[4] .ENV CONFIGURATION STATUS")
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

required = {
    "ENCRYPTION_KEY":        "Generate: python -m src.gen_key  OR  see instructions below",
    "AWS_ACCESS_KEY_ID":     "Your IAM Access Key from AWS Console",
    "AWS_SECRET_ACCESS_KEY": "Your IAM Secret Key from AWS Console",
    "EXOTEL_API_KEY":        "From Exotel Dashboard -> API Keys",
    "EXOTEL_API_TOKEN":      "From Exotel Dashboard -> API Keys",
    "EXOTEL_SID":            "From Exotel Dashboard (Account SID)",
    "EXOTEL_SUBDOMAIN":      "e.g. api.exotel.com",
    "WS_PUBLIC_URL":         "wss://your-ec2-ip/exotel-stream (NOT a placeholder)",
}
optional_keys = [
    "EXOTEL_WS_SECRET", "HEALTH_CHECK_TOKEN",
    "MEMORY_ID", "KB_ID", "GOOGLE_SHEET_ID",
]
missing_required = []
for k, hint in required.items():
    val = os.environ.get(k, "")
    placeholder = any(x in val for x in ["YOUR_EC2", "your-domain", "your_access", "mock"])
    if not val or placeholder:
        missing_required.append(k)
        print(f"  NOT SET  {k}")
    else:
        masked = val[:4] + "..." + val[-4:] if len(val) > 10 else "***"
        print(f"  SET      {k} = {masked}")

print()
print("[5] OPTIONAL CONFIG")
for k in optional_keys:
    val = os.environ.get(k, "")
    print(f"  {'SET' if val else 'not set (optional)':22} {k}")

# ---- Summary ----
print()
print("=" * 55)
if syntax_errors == 0 and all_code_ok and assets_ok and not missing_required:
    print("  STATUS: FULLY READY TO DEPLOY")
else:
    print("  STATUS: ACTION NEEDED")
    if syntax_errors > 0:
        print(f"    - {syntax_errors} syntax error(s)")
    if not all_code_ok:
        print(f"    - Some code fixes not detected (see FAIL above)")
    if not assets_ok:
        print(f"    - Missing PCM assets")
    if missing_required:
        print(f"    - {len(missing_required)} required .env value(s) not set:")
        for k in missing_required:
            print(f"        {k}")
print("=" * 55)

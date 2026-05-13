import pathlib

root = pathlib.Path('.')

def read(f):
    try:
        return pathlib.Path(f).read_text(encoding='utf-8', errors='replace')
    except:
        return ''

issues = []

v = read('scripts/verify_e2e.py')
if v.startswith('\ufeff'):
    issues.append(('CRIT', 'scripts/verify_e2e.py', 'BOM (U+FEFF) makes file un-importable. Causes SyntaxError on import.'))

ts = read('src/transcript_store.py')
if '_table = boto3.Session' in ts:
    issues.append(('HIGH', 'src/transcript_store.py', 'Line 15: boto3 DynamoDB resource created at module-load time before .env is parsed. AWS creds may be empty.'))

ap = read('src/analytics/processor.py')
if 'def __init__(self):' in ap and 'bedrock_runtime = boto3.client' in ap:
    issues.append(('MED', 'src/analytics/processor.py', 'boto3 client in __init__ missing tcp_keepalive/pool config (inconsistent with OPT-07).'))

hc = read('src/diagnostics/health.py')
if 'Connected (IAM Validated)' in hc:
    issues.append(('MED', 'src/diagnostics/health.py', 'check_aws() only calls STS identity, never tests Bedrock. Can show HEALTHY when Bedrock is IAM-blocked.'))

if 'st_ctime' in hc:
    issues.append(('LOW', 'src/diagnostics/health.py', 'Line 91: timestamp uses cwd stat ctime, not current time. Should be datetime.now().isoformat().'))

srv = read('src/server.py')
if 'last_activity_time = time.time() # Reset during tool calls' in srv:
    issues.append(('HIGH', 'src/server.py', 'Line 789: idle_monitor assigns last_activity_time without nonlocal. Creates local var silently => timer never resets during tool calls => premature escalation.'))

if 'call_start_time.astimezone(IST)' in srv:
    issues.append(('LOW', 'src/server.py', 'Line 1147: duration calc mixes IST datetime.now with astimezone(IST). Use timezone.utc consistently.'))

se = read('src/integrations/sync_engine.py')
if 'socket.gethostbyname' in se:
    issues.append(('MED', 'src/integrations/sync_engine.py', 'is_safe_url() calls blocking socket.gethostbyname() inside async function. Blocks event loop during SSRF checks.'))

ls = read('src/integrations/local_sink.py')
if 'pass' in ls and 'Anti-Spam' in ls:
    issues.append(('LOW', 'src/integrations/local_sink.py', 'Lines 41-44: Anti-spam CRITICAL duplicate check is a no-op (pass). Duplicate emergency rows fill CSV.'))

nc = read('src/nova_client.py')
if 'max_chunks_per_batch = 5' in nc:
    issues.append(('LOW', 'src/nova_client.py', 'max_chunks_per_batch=5 but queue is now 10 (OPT-09). Should be 10 for consistent drain rate.'))

me = read('src/mock_engine.py')
if 'audioOutput' not in me:
    issues.append(('LOW', 'src/mock_engine.py', 'MockS2SStream never dispatches audioOutput events. Demo callers hear silence in mock mode.'))

tl = read('src/tools.py')
kb_count = tl.count('_kb_client = boto3.client')
if kb_count > 1:
    issues.append(('MED', 'src/tools.py', f'_kb_client created {kb_count}x (duplicate from OPT-07 refactor). Second definition overwrites first.'))

tc = read('src/types_config.py')
if 'max_queue_size' in tc:
    issues.append(('MED', 'src/types_config.py', 'max_queue_size added to AudioConfiguration (wrong dataclass). It is a Bedrock protocol config struct, not a buffer config. Belongs in StreamSession only.'))

print(f'Issues found: {len(issues)}')
print()
for sev, file, desc in sorted(issues, key=lambda x: ['CRIT','HIGH','MED','LOW'].index(x[0])):
    print(f'[{sev}]  {file}')
    print(f'       {desc}')
    print()

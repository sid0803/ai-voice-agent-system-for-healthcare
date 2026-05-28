import time
import socket
import sys

TARGET_IP = "15.206.93.221"
DOMAIN = "voice.indiiserve.ai"
MAX_ATTEMPTS = 60  # 30 minutes max (60 * 30 seconds)

print(f"[RUNNING] Waiting for {DOMAIN} to resolve to {TARGET_IP}...")
for attempt in range(1, MAX_ATTEMPTS + 1):
    try:
        resolved_ip = socket.gethostbyname(DOMAIN)
        print(f"Attempt {attempt}: Resolved to {resolved_ip}")
        if resolved_ip == TARGET_IP:
            print(f"[SUCCESS] DNS has successfully updated! {DOMAIN} points to {TARGET_IP}")
            sys.exit(0)
    except socket.gaierror:
        print(f"Attempt {attempt}: Resolution failed")
    time.sleep(30)

print("[TIMEOUT] DNS did not update within 30 minutes.")
sys.exit(1)

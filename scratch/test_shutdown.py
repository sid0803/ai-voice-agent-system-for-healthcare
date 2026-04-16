import subprocess
import time
import requests
import os
import signal
import sys

# Start the uvicorn server as a subprocess
print("Starting Uvicorn...")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "src.server:app", "--host", "127.0.0.1", "--port", "8080"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

# Wait for server to be ready
time.sleep(3)

# Verify health endpoint to ensure clean start
try:
    resp = requests.get("http://127.0.0.1:8080/health")
    if resp.status_code == 200:
        print("Healthcheck Verified: Uvicorn Started Successfully on Port 8080")
    else:
        print("Healthcheck Failed")
except Exception as e:
    print(f"Healthcheck Request Failed: {e}")

# Inject a mock artificial task into the server's tracking set 
# To do this cleanly across processes, we'll just stop the server with CTRL_C_EVENT to see standard behavior
# Since Windows doesn't fully support SIGTERM gracefully through python subprocess without Win32 tricks, 
# we'll terminate the process explicitly and check the tail end of logs.

print("Sending Shutdown Signal...")
if os.name == 'nt':
    proc.send_signal(signal.CTRL_C_EVENT)
else:
    proc.send_signal(signal.SIGINT)

try:
    stdout, _ = proc.communicate(timeout=10)
    print("--- SERVER LOGS ---")
    for line in stdout.splitlines()[-15:]:
        print(line)
        if "Waiting for" in line and "pending background tasks" in line:
            print("SUCCESS: Found tracking statement for pending tasks during shutdown.")
    print("--- END SERVER LOGS ---")
except subprocess.TimeoutExpired:
    print("Process didn't shut down in time, killing.")
    proc.kill()

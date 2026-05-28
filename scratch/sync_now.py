import os
import sys
import time

# Load .env file
current_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, ".."))

env_path = os.path.join(repo_root, '.env')
if not os.path.exists(env_path):
    env_path = '/home/ubuntu/indiiserve/.env'

if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

sys.path.append(repo_root)
sys.path.append('/home/ubuntu/indiiserve')

from src.integrations.tenant_manager import tenant_manager

# Trigger get_hospital_data to start the background sync
data = tenant_manager.get_hospital_data("apollo_metro")
print("Triggered get_hospital_data. Waiting 10 seconds for background thread to write to DynamoDB...")
time.sleep(10)
print("Checking DynamoDB state now...")

d = tenant_manager._get_from_db("apollo_metro")
if d:
    print(f"Name in DB: {d.get('name')}")
    print(f"Doctors count: {len(d.get('doctors', []))}")
    print(f"Services count: {len(d.get('services', []))}")
else:
    print("Failed to read from DB!")

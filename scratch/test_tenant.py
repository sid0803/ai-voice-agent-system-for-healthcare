import os
import sys

# Load .env file
env_path = '/home/ubuntu/indiiserve/.env'
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

sys.path.append('/home/ubuntu/indiiserve')
from src.integrations.tenant_manager import tenant_manager

print("Current env HOSPITAL_ID:", os.environ.get("HOSPITAL_ID"))
print("-" * 50)
data = tenant_manager.get_hospital_data("apollo_metro")
print("Name resolved:", data.get("name"))
print("ID resolved:", data.get("id"))
print("Status resolved:", data.get("status"))
print("Number of FAQ items:", len(data.get("faq", [])))
print("Number of services:", len(data.get("services", [])))
print("Number of doctors:", len(data.get("doctors", [])))

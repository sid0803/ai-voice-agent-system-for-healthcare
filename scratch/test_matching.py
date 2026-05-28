import os
import sys

# Try current directory first, then fallback to /home/ubuntu/indiiserve
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
from src.tools import hospital_info, doctor_availability

def run_tests():
    # Force apollo_metro for the test
    tenant_manager.current_tenant = "apollo_metro"
    
    # Verify dataset is loaded correctly
    data = tenant_manager.get_hospital_data("apollo_metro")
    print(f"Tenant Loaded: {data.get('name')}")
    print(f"Doctors count: {len(data.get('doctors', []))}")
    print(f"Services count: {len(data.get('services', []))}")
    print("-" * 50)

    test_queries = [
        # Scans and diagnostics
        ("how much does a brain mri cost", "hospital_info"),
        ("what about mri scan", "hospital_info"),
        ("spine mri price", "hospital_info"),
        ("ct scan charges", "hospital_info"),
        ("do i need to fast before a contrast ct scan", "hospital_info"),
        ("what is the price of thyroid profile test", "hospital_info"),
        ("Complete Blood Count (CBC) charges", "hospital_info"),
        
        # Parking and visiting hours
        ("parking availability or charges", "hospital_info"),
        ("is parking free for patients", "hospital_info"),
        ("what are the icu visiting hours", "hospital_info"),
        ("general ward visiting time", "hospital_info"),
        
        # Room rents
        ("what room types are available and what is the rent", "hospital_info"),
        ("ICU room rate per day", "hospital_info"),
        
        # Doctor availability
        ("is there any cardiologist available", "doctor_availability"),
        ("who is your neurologist", "doctor_availability"),
        ("is Dr. Megha Rao available", "doctor_availability"),
        ("knee pain specialty doctor", "doctor_availability"),
        ("is there a pediatrician available", "doctor_availability"),
    ]

    for query, tool_name in test_queries:
        args = {"query": query}
        print(f"\nQUERY: '{query}' -> TOOL: {tool_name}")
        if tool_name == "hospital_info":
            res = hospital_info(args, "apollo_metro")
        else:
            res = doctor_availability(args, "apollo_metro")
        ans = res.get("answer", "")
        print(f"RESPONSE: {ans}")
        # Verify it didn't fall back to generic "Standard Tier-2" or "Unfortunately, the system isn't providing the specific information"
        assert "Standard Tier-2" not in ans, "FAILED: resolved to default_tier2 fallback!"
        assert "is located at" in ans or "cost" in ans or "price" in ans or "rate" in ans or "Rs." in ans or "available" in ans or "visiting" in ans or "accept" in ans or "wellness" in ans or "specialists" in ans or "Dr." in ans, f"FAILED: fallback text detected or answer is incomplete: {ans}"
        print("RESULT: PASS")

if __name__ == "__main__":
    run_tests()

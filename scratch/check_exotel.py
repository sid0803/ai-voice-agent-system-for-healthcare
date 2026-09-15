import os
import sys
import requests
import json
from datetime import datetime

api_key = os.environ.get("EXOTEL_API_KEY")
api_token = os.environ.get("EXOTEL_API_TOKEN")
sid = os.environ.get("EXOTEL_SID", "indiiserve1")
subdomain = os.environ.get("EXOTEL_SUBDOMAIN", "api.exotel.com")

if not api_key or not api_token:
    print("[ERROR] Missing EXOTEL_API_KEY or EXOTEL_API_TOKEN in environment variables.")
    sys.exit(1)

url = f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{sid}/Calls.json"

try:
    # Query recent call log list
    resp = requests.get(url, params={"Limit": 50})
    if resp.status_code == 200:
        calls = resp.json().get("Calls", [])
        print(f"Total calls retrieved: {len(calls)}")
        print("-" * 80)
        # Print info for the most recent 15 calls
        for c in calls[:15]:
            print(f"SID: {c.get('Sid')}")
            print(f"  Created: {c.get('DateCreated')}")
            print(f"  From:    {c.get('From')}")
            print(f"  To:      {c.get('To')}")
            print(f"  Phone:   {c.get('PhoneNumber')}")
            print(f"  Status:  {c.get('Status')}")
            print(f"  Reason:  {c.get('AnsweredBy') or 'N/A'}")
            print(f"  Duration: {c.get('Duration')}s")
            print("-" * 80)
    else:
        print(f"Failed to fetch calls: HTTP {resp.status_code}")
        print(resp.text)
except Exception as e:
    print(f"Error: {e}")

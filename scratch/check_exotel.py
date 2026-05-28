import requests
import json
from datetime import datetime

api_key = "d341b12bf96f67d419047f72e7d0fdd142d3e80b2ecc7236"
api_token = "c8a271d43bd6878fb25b2d7a8641416b75d466cb24692280"
sid = "indiiserve1"

url = f"https://{api_key}:{api_token}@api.exotel.com/v1/Accounts/{sid}/Calls.json"

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

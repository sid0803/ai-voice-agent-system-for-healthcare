import boto3
import json
import os
from dotenv import load_dotenv

# Load env file from the current directory if it exists
load_dotenv('/home/ubuntu/indiiserve/.env')

def main():
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "InDiiServe_Asha_Healthcare_Transcripts_NEW")
    print(f"Scanning table: {table_name}")
    t = boto3.resource('dynamodb', region_name='ap-south-1').Table(table_name)
    items = t.scan()['Items']
    print(f"Total items in table: {len(items)}")
    
    # Sort by timestamp descending
    # Format of timestamp: "2026-05-27 20:16:53 IST" or ISO format
    items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    for i in items[:3]:
        print('='*80)
        print(f"Phone: {i.get('phone_number')} | Timestamp: {i.get('timestamp')} | Duration: {i.get('duration')} | Session: {i.get('session_id')}")
        print('='*80)
        for m in i.get('transcript', []):
            role = m.get('role', '')
            text = m.get('text') or m.get('content', '')
            print(f"[{role}]: {text}")

if __name__ == '__main__':
    main()

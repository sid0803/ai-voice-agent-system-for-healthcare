import os
import boto3

# Load .env file
env_path = '/home/ubuntu/indiiserve/.env'
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

table_name = os.environ.get("DYNAMODB_TABLE_NAME", "InDiiServe_Asha_Healthcare_Transcripts_NEW")
t = boto3.resource('dynamodb', region_name='ap-south-1').Table(table_name)
items = t.scan()['Items']
items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
i = items[0]
print("Session ID:", i.get('session_id'))
print("Timestamp:", i.get('timestamp'))
print("Hospital ID:", i.get('hospital_id', 'unknown'))
print("Phone Number:", i.get('phone_number', 'unknown'))
print("-" * 40)
for m in i.get('transcript', []):
    print(m.get('role', '') + ': ' + (m.get('text') or m.get('content', '')))

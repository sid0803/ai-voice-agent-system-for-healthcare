import os
import sys
import boto3

LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")

if not os.path.exists(ENV_FILE):
    print("[ERROR] .env file not found")
    sys.exit(1)

aws_access_key = None
aws_secret_key = None

with open(ENV_FILE, "r") as f:
    for line in f:
        if line.startswith("AWS_ACCESS_KEY_ID="):
            aws_access_key = line.split("=")[1].strip()
        elif line.startswith("AWS_SECRET_ACCESS_KEY="):
            aws_secret_key = line.split("=")[1].strip()

# Establish a session to list regions
temp_session = boto3.Session(
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="us-east-1"
)
ec2_client = temp_session.client("ec2")

try:
    regions_resp = ec2_client.describe_regions()
    regions = [r["RegionName"] for r in regions_resp["Regions"]]
except Exception as e:
    print(f"Error describing regions: {e}")
    sys.exit(1)

print(f"Found {len(regions)} regions. Scanning all of them...")

for region in regions:
    session = boto3.Session(
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=region
    )
    ec2 = session.client("ec2")
    try:
        response = ec2.describe_instances()
        reservations = response.get("Reservations", [])
        for res in reservations:
            for inst in res.get("Instances", []):
                inst_id = inst.get("InstanceId")
                state = inst.get("State", {}).get("Name")
                ip = inst.get("PublicIpAddress", "N/A")
                
                # Extract Name tag
                name = "N/A"
                for tag in inst.get("Tags", []):
                    if tag.get("Key") == "Name":
                        name = tag.get("Value")
                
                if ip == "65.2.152.5" or "indiiserve" in name.lower() or state == "running":
                    print(f"[{region}] Name: {name} | ID: {inst_id} | State: {state} | Public IP: {ip}")
    except Exception as e:
        # Ignore regions that are not enabled or have authorization errors
        pass
print("Scan complete.")

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

session = boto3.Session(
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="ap-south-1"
)
ec2 = session.client("ec2")

try:
    addresses = ec2.describe_addresses()
    for addr in addresses.get("Addresses", []):
        print(f"Public IP:  {addr.get('PublicIp')}")
        print(f"Allocation: {addr.get('AllocationId')}")
        print(f"Instance:   {addr.get('InstanceId')}")
        print(f"Domain:     {addr.get('Domain')}")
        print("-" * 30)
except Exception as e:
    print(f"Error describing addresses: {e}")

import os
import sys
import boto3

from dotenv import load_dotenv

LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

session = boto3.Session(region_name=os.environ.get("AWS_REGION", "ap-south-1"))
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

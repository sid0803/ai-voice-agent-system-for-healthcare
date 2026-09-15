import os
import sys
import time
import boto3

from dotenv import load_dotenv

LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

session = boto3.Session(region_name=os.environ.get("AWS_REGION", "ap-south-1"))
ec2 = session.client("ec2")

instance_id = os.environ.get("TARGET_INSTANCE_ID", "")
elastic_ip = "13.232.197.108"

print(f"[RUNNING] Terminating old instance {instance_id}...")
try:
    ec2.terminate_instances(InstanceIds=[instance_id])
    print(f"[OK] Termination request submitted for {instance_id}.")
except Exception as e:
    print(f"[ERROR] Could not terminate instance: {e}")

print("[RUNNING] Waiting for instance to terminate before releasing Elastic IP...")
# Wait for instance to terminate (up to 3 minutes)
for i in range(36):
    try:
        status = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]["State"]["Name"]
        print(f"Current state: {status}")
        if status == "terminated":
            break
    except Exception as e:
        print(f"Error checking state: {e}")
    time.sleep(5)

print(f"[RUNNING] Checking Elastic IP {elastic_ip} allocation...")
try:
    addresses = ec2.describe_addresses(PublicIps=[elastic_ip])
    if addresses.get("Addresses"):
        alloc_id = addresses["Addresses"][0]["AllocationId"]
        print(f"[RUNNING] Releasing Elastic IP allocation: {alloc_id}...")
        ec2.release_address(AllocationId=alloc_id)
        print(f"[OK] Released Elastic IP {elastic_ip} successfully.")
    else:
        print(f"[INFO] Elastic IP {elastic_ip} not found or already released.")
except Exception as e:
    print(f"[ERROR] Could not release Elastic IP: {e}")

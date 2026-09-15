import os
import sys
import boto3

from dotenv import load_dotenv

LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

session = boto3.Session(region_name=os.environ.get("AWS_REGION_VIRGINIA", "us-east-1"))
ec2 = session.client("ec2")

instance_id = os.environ.get("TARGET_VIRGINIA_INSTANCE_ID", "")

print(f"[RUNNING] Terminating old Virginia instance {instance_id}...")
try:
    ec2.terminate_instances(InstanceIds=[instance_id])
    print(f"[OK] Successfully submitted termination request for Virginia instance {instance_id}.")
except Exception as e:
    print(f"[ERROR] Could not terminate instance: {e}")

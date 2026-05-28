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
    region_name="us-east-1"
)
ec2 = session.client("ec2")

instance_id = "i-050fe50ea149ab8ef"

print(f"[RUNNING] Terminating old Virginia instance {instance_id}...")
try:
    ec2.terminate_instances(InstanceIds=[instance_id])
    print(f"[OK] Successfully submitted termination request for Virginia instance {instance_id}.")
except Exception as e:
    print(f"[ERROR] Could not terminate instance: {e}")

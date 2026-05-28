import os
import sys
import boto3

# Define local paths
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")

# 1. Parse .env for AWS Credentials
if not os.path.exists(ENV_FILE):
    print(f"[ERROR] Local .env file not found at {ENV_FILE}")
    sys.exit(1)

aws_access_key = None
aws_secret_key = None

with open(ENV_FILE, "r") as f:
    for line in f:
        if line.startswith("AWS_ACCESS_KEY_ID="):
            aws_access_key = line.split("=")[1].strip()
        elif line.startswith("AWS_SECRET_ACCESS_KEY="):
            aws_secret_key = line.split("=")[1].strip()

if not aws_access_key or not aws_secret_key:
    print("[ERROR] AWS credentials not found in local .env file")
    sys.exit(1)

# 2. Describe instances in Mumbai (ap-south-1) and Virginia (us-east-1)
regions = ["ap-south-1", "us-east-1"]

for region in regions:
    print(f"\n=== Region: {region} ===")
    session = boto3.Session(
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=region
    )
    ec2 = session.client("ec2")
    
    try:
        response = ec2.describe_instances()
        reservations = response.get("Reservations", [])
        if not reservations:
            print("No instances found.")
            continue
            
        for res in reservations:
            for inst in res.get("Instances", []):
                inst_id = inst.get("InstanceId")
                state = inst.get("State", {}).get("Name")
                ip = inst.get("PublicIpAddress", "N/A")
                private_ip = inst.get("PrivateIpAddress", "N/A")
                dns = inst.get("PublicDnsName", "N/A")
                az = inst.get("Placement", {}).get("AvailabilityZone")
                launch_time = inst.get("LaunchTime")
                
                # Extract Name tag
                name = "N/A"
                for tag in inst.get("Tags", []):
                    if tag.get("Key") == "Name":
                        name = tag.get("Value")
                
                print(f"Name:         {name}")
                print(f"Instance ID:  {inst_id}")
                print(f"State:        {state}")
                print(f"Public IP:    {ip}")
                print(f"Private IP:   {private_ip}")
                print(f"Public DNS:   {dns}")
                print(f"AZ:           {az}")
                print(f"Launch Time:  {launch_time}")
                print("-" * 40)
    except Exception as e:
        print(f"Error querying region {region}: {e}")

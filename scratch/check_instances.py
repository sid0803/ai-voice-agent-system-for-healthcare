import os
import sys
import boto3

from dotenv import load_dotenv

# Define local paths
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

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

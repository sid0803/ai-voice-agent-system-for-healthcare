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

# Check in all regions
regions = ["ap-south-1", "us-east-1"]

for region in regions:
    print(f"\nChecking region: {region}")
    session = boto3.Session(
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=region
    )
    ec2 = session.client("ec2")
    
    # 1. Search by Elastic IP
    try:
        addresses = ec2.describe_addresses(PublicIps=["65.2.152.5"])
        for addr in addresses.get("Addresses", []):
            print(f"Found Elastic IP in {region}:")
            print(addr)
    except ec2.exceptions.ClientError as e:
        if "InvalidAddress.NotFound" not in str(e):
            print(f"Error querying address: {e}")
            
    # 2. Search by Instance Public IP
    try:
        response = ec2.describe_instances(
            Filters=[{"Name": "ip-address", "Values": ["65.2.152.5"]}]
        )
        reservations = response.get("Reservations", [])
        for res in reservations:
            for inst in res.get("Instances", []):
                print(f"Found Instance in {region}: {inst.get('InstanceId')} (State: {inst.get('State', {}).get('Name')})")
    except Exception as e:
        print(f"Error querying instances: {e}")

    # 3. Search Network Interfaces
    try:
        response = ec2.describe_network_interfaces(
            Filters=[{"Name": "association.public-ip", "Values": ["65.2.152.5"]}]
        )
        interfaces = response.get("NetworkInterfaces", [])
        for nic in interfaces:
            print(f"Found Network Interface in {region}: {nic.get('NetworkInterfaceId')}")
            print(nic.get("Association"))
    except Exception as e:
        print(f"Error querying network interfaces: {e}")

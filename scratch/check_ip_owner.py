import os
import sys
import boto3

from dotenv import load_dotenv

LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

# Check in all regions
regions = ["ap-south-1", "us-east-1"]

for region in regions:
    print(f"\nChecking region: {region}")
    session = boto3.Session(region_name=region)
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

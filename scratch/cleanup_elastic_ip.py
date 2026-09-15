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

eip = os.environ.get("TARGET_EIP", "")

try:
    # Find the AllocationId and AssociationId of the Elastic IP
    addresses = ec2.describe_addresses(PublicIps=[eip])["Addresses"]
    if not addresses:
        print(f"Elastic IP {eip} not found.")
        sys.exit(0)
        
    addr = addresses[0]
    allocation_id = addr.get("AllocationId")
    association_id = addr.get("AssociationId")
    instance_id = addr.get("InstanceId")
    
    print(f"Elastic IP:      {eip}")
    print(f"Allocation ID:   {allocation_id}")
    print(f"Association ID:  {association_id}")
    print(f"Associated with: {instance_id}")
    
    # 1. Disassociate if associated
    if association_id:
        print(f"Disassociating Elastic IP from instance {instance_id}...")
        ec2.disassociate_address(AssociationId=association_id)
        print("Successfully disassociated.")
    else:
        print("Elastic IP is not associated.")
        
    # 2. Release the Elastic IP
    if allocation_id:
        print(f"Releasing Elastic IP {eip}...")
        ec2.release_address(AllocationId=allocation_id)
        print("Successfully released.")
    else:
        print("No Allocation ID found to release.")
        
except Exception as e:
    print(f"Error cleaning up Elastic IP: {e}")
    sys.exit(1)

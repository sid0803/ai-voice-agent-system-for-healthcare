import os
import sys
import boto3

# Load credentials from .env
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")

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

eip = "65.2.88.28"

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

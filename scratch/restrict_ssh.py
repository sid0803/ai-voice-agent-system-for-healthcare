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

instance_id = "i-0d18a8976d04ab894"
admin_ip = "103.182.106.199/32"

try:
    # 1. Describe instance to get security groups
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    groups = resp["Reservations"][0]["Instances"][0]["SecurityGroups"]
    
    for group in groups:
        group_id = group["GroupId"]
        group_name = group["GroupName"]
        print(f"Checking Security Group: {group_name} ({group_id})")
        
        # Describe security group rules
        sg_info = ec2.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0]
        
        # Look for SSH (port 22) rule from 0.0.0.0/0
        ip_permissions = sg_info.get("IpPermissions", [])
        ssh_from_world_exists = False
        
        for perm in ip_permissions:
            if perm.get("FromPort") == 22 and perm.get("ToPort") == 22:
                # Check if it has 0.0.0.0/0
                for ip_range in perm.get("IpRanges", []):
                    if ip_range.get("CidrIp") == "0.0.0.0/0":
                        ssh_from_world_exists = True
                        break
        
        if ssh_from_world_exists:
            print(f"Found open SSH rule from 0.0.0.0/0. Revoking...")
            ec2.revoke_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                    }
                ]
            )
            print("Successfully revoked SSH from world (0.0.0.0/0)")
        else:
            print("No open SSH rule from 0.0.0.0/0 found.")
            
        # Add rule for admin IP
        admin_rule_exists = False
        for perm in ip_permissions:
            if perm.get("FromPort") == 22 and perm.get("ToPort") == 22:
                for ip_range in perm.get("IpRanges", []):
                    if ip_range.get("CidrIp") == admin_ip:
                        admin_rule_exists = True
                        break
                        
        if not admin_rule_exists:
            print(f"Authorizing SSH for admin IP: {admin_ip}")
            ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[
                    {
                        'IpProtocol': 'tcp',
                        'FromPort': 22,
                        'ToPort': 22,
                        'IpRanges': [{'CidrIp': admin_ip, 'Description': 'Admin SSH Access'}]
                    }
                ]
            )
            print(f"Successfully authorized SSH from {admin_ip}")
        else:
            print(f"SSH rule for admin IP {admin_ip} already exists.")
            
except Exception as e:
    print(f"Error updating security group rules: {e}")
    sys.exit(1)

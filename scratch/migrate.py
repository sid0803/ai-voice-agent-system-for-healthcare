import os
import sys
import time
import socket
import subprocess
import boto3
from cryptography.hazmat.primitives import serialization

# Define local paths
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(LOCAL_DIR, ".env")
DB_FILE = os.path.join(LOCAL_DIR, "indiiserve_demo.db")
KEY_PATH = r"C:\Users\sid08\Downloads\my-server-key.pem"

print("==================================================")
print(">> InDiiServe Mumbai Server Migration Automation")
print("==================================================")

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

# 2. Check local private key file
if not os.path.exists(KEY_PATH):
    print(f"[ERROR] SSH key file not found at {KEY_PATH}")
    sys.exit(1)

# 3. Setup boto3 client
session = boto3.Session(
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="ap-south-1"
)
ec2 = session.client("ec2")

# 4. Import key pair if not exists in Mumbai
try:
    ec2.describe_key_pairs(KeyNames=["my-server-key"])
    print("[OK] Key pair 'my-server-key' already exists in ap-south-1 (Mumbai).")
except ec2.exceptions.ClientError:
    print("[RUNNING] Importing local 'my-server-key' to ap-south-1 (Mumbai)...")
    with open(KEY_PATH, "rb") as key_file:
        private_key = serialization.load_pem_private_key(key_file.read(), password=None)
    public_key = private_key.public_key()
    openssh_pub = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH
    )
    ec2.import_key_pair(KeyName="my-server-key", PublicKeyMaterial=openssh_pub)
    print("[OK] Successfully imported key pair 'my-server-key'.")

# 5. Security Group Setup
vpc_id = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
sg_name = "indiiserve-sg"
sg_id = None

try:
    sg_response = ec2.describe_security_groups(GroupNames=[sg_name])
    sg_id = sg_response["SecurityGroups"][0]["GroupId"]
    print(f"[OK] Security group '{sg_name}' already exists ({sg_id}).")
except ec2.exceptions.ClientError:
    print(f"[RUNNING] Creating security group '{sg_name}' in VPC {vpc_id}...")
    sg_response = ec2.create_security_group(
        GroupName=sg_name,
        Description="Security Group for InDiiServe Voice Agent",
        VpcId=vpc_id
    )
    sg_id = sg_response["GroupId"]
    
    # Authorize ingress rules
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            # SSH (Port 22)
            {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            # HTTP (Port 80)
            {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            # HTTPS (Port 443)
            {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
        ]
    )
    print(f"[OK] Created and configured security group '{sg_name}'.")

# 6. Find latest Ubuntu 24.04 LTS (Noble) AMI - comes with Python 3.12 default
print("[RUNNING] Querying latest Ubuntu 24.04 LTS (Noble) AMI in ap-south-1...")
ami_response = ec2.describe_images(
    Filters=[
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"Name": "state", "Values": ["available"]}
    ],
    Owners=["099720109477"] # Canonical
)
images = sorted(ami_response["Images"], key=lambda x: x["CreationDate"], reverse=True)
latest_ami = images[0]["ImageId"]
print(f"[OK] Found latest Ubuntu 24.04 AMI: {latest_ami}")

# 7. Launch EC2 instance
print("[RUNNING] Launching EC2 instance (t3.medium, 20GB storage)...")
run_response = ec2.run_instances(
    ImageId=latest_ami,
    InstanceType="t3.medium",
    KeyName="my-server-key",
    MinCount=1,
    MaxCount=1,
    SecurityGroupIds=[sg_id],
    BlockDeviceMappings=[
        {
            "DeviceName": "/dev/sda1",
            "Ebs": {
                "VolumeSize": 20,
                "VolumeType": "gp3",
                "DeleteOnTermination": True
            }
        }
    ],
    TagSpecifications=[
        {
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "indiiserve-mumbai-voice-agent"}]
        }
    ]
)
instance_id = run_response["Instances"][0]["InstanceId"]
print(f"[RUNNING] Instance launched ({instance_id}). Waiting for it to enter 'running' state...")

# Wait for instance to run
waiter = ec2.get_waiter("instance_running")
waiter.wait(InstanceIds=[instance_id])

# Describe instance to get IP
instance_desc = ec2.describe_instances(InstanceIds=[instance_id])
public_ip = instance_desc["Reservations"][0]["Instances"][0]["PublicIpAddress"]
print(f"[OK] Instance is running! Public IP: {public_ip}")

# 8. Wait for SSH to open
print("[RUNNING] Waiting for SSH service to boot up on the server...")
def wait_for_ssh(ip, port=22, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection((ip, port), timeout=5)
            s.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(5)
    return False

if wait_for_ssh(public_ip):
    print("[OK] SSH is online and accepting connections.")
else:
    print("[ERROR] SSH did not come online in time.")
    sys.exit(1)

# Adding a brief delay to ensure SSH keys exchange fully initialized
time.sleep(5)

# 9. Transfer configuration and database via SCP
print("[RUNNING] Uploading configuration and database files to the server...")

def run_local_cmd(cmd):
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")
        print(f"Error output: {result.stderr}")
        return False
    return True

# Ensure remote home directory is writeable and transfer files
scp_env_cmd = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{ENV_FILE}" ubuntu@{public_ip}:/home/ubuntu/.env'
scp_db_cmd = f'scp -i "{KEY_PATH}" -o StrictHostKeyChecking=no "{DB_FILE}" ubuntu@{public_ip}:/home/ubuntu/indiiserve_demo.db'

if not run_local_cmd(scp_env_cmd) or not run_local_cmd(scp_db_cmd):
    print("[ERROR] File transfer failed.")
    sys.exit(1)

print("[OK] Configuration and database uploaded successfully.")

# 10. Execute remote setup script via SSH
print("[RUNNING] Provisioning software and dependencies on the Mumbai server (this will take 3-4 minutes)...")

# Write the remote shell commands
remote_setup_script = """
set -e
echo "1. Installing system updates and dependencies..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3.12 python3.12-venv python3-pip git curl nginx build-essential python3-dev unzip

echo "2. Cloning project code..."
rm -rf /home/ubuntu/indiiserve
git clone https://github.com/sid0803/ai-voice-agent-system-for-healthcare.git /home/ubuntu/indiiserve
cd "/home/ubuntu/indiiserve"

echo "3. Copying configuration files..."
mv /home/ubuntu/.env .env
mv /home/ubuntu/indiiserve_demo.db indiiserve_demo.db

echo "4. Setting up python virtual environment..."
python3.12 -m venv linux_venv
source linux_venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "5. Creating systemd service..."
sudo tee /etc/systemd/system/indiiserve.service << 'EOF'
[Unit]
Description=InDiiServe Nova Sonic Voice Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/indiiserve
Environment=PYTHONPATH=.
EnvironmentFile=/home/ubuntu/indiiserve/.env
ExecStart=/usr/bin/bash -lc 'cd "/home/ubuntu/indiiserve" && exec ./linux_venv/bin/python -m uvicorn src.server:app --host 127.0.0.1 --port 9000 --workers 1 --log-level info'
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable indiiserve
sudo systemctl start indiiserve

echo "6. Configuring Nginx reverse proxy..."
sudo tee /etc/nginx/sites-available/indiiserve << 'EOF'
server {
    listen 80;
    server_name voice.indiiserve.ai;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/indiiserve /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

echo "[OK] Server setup complete!"
"""

# Write script to temporary file on remote host and run it
ssh_cmd = f'ssh -i "{KEY_PATH}" -o StrictHostKeyChecking=no ubuntu@{public_ip} "cat << \'EOF\' > /tmp/setup.sh\n{remote_setup_script}\nEOF\nbash /tmp/setup.sh"'

setup_result = subprocess.run(ssh_cmd, shell=True, text=True, capture_output=True)
if setup_result.returncode != 0:
    print("[ERROR] Server provisioning failed.")
    print(setup_result.stderr)
    sys.exit(1)

print(setup_result.stdout)

print("\n==================================================")
print("SUCCESS! Mumbai Server Setup Completed!")
print("==================================================")
print(f"New Server IP: {public_ip}")
print("Your next steps:")
print("1. Go to your DNS provider (e.g. Hostinger) and point voice.indiiserve.ai to: " + public_ip)
print("2. Once DNS propagates (1-2 mins), SSH into your new server and run: ")
print(f"   ssh -i \"{KEY_PATH}\" ubuntu@{public_ip} \"sudo certbot --nginx -d voice.indiiserve.ai\"")
print("==================================================")

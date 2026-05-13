# 🚀 AWS EC2 Deployment Guide
## InDiiServe Nova Sonic Voice Agent — End-to-End Production Deployment
**Target:** Call an Exotel number → Asha AI answers in real-time  
**Access Model:** Direct AWS Console (no IEM/SSO required)

---

> [!IMPORTANT]
> This guide is written for **direct AWS Console + EC2 SSH access** without AWS SSO or IAM Identity Center (IEM). You will use IAM User credentials directly. Follow every step in sequence.

---

## PHASE 1: AWS Account & IAM Setup (AWS Console)

### Step 1.1 — Create an IAM User for Deployment

1. Go to [AWS Console → IAM → Users](https://console.aws.amazon.com/iam/home#/users)
2. Click **"Create user"**
3. Username: `indiiserve-deploy`
4. Check **"Provide user access to the AWS Management Console"** → No (we only need programmatic access)
5. Click **Next → Next → Create user**
6. Click the user name → **Security credentials** tab
7. Click **"Create access key"** → Select **"Application running on an AWS compute service"**
8. Save the **Access Key ID** and **Secret Access Key** — you will need these for the `.env` file

### Step 1.2 — Attach IAM Policies

Still in the IAM user page, click **"Add permissions" → "Attach policies directly"**. Attach these managed policies:

| Policy Name | Why |
|-------------|-----|
| `AmazonBedrockFullAccess` | Nova Sonic, Nova Lite, Titan Embeddings, Knowledge Base |
| `AmazonDynamoDBFullAccess` | Call transcript storage |
| `AmazonRDSFullAccess` | Analytics database (or `AmazonRDSDataFullAccess` if using Data API) |
| `SecretsManagerReadWrite` | Encryption key and API token storage |
| `AmazonEC2FullAccess` | EC2 instance management |

> [!NOTE]
> For production, replace these broad policies with custom scoped policies. This broad set is for initial deployment speed.

### Step 1.3 — Enable Bedrock Model Access

1. Go to [AWS Console → Bedrock → Model access](https://console.aws.amazon.com/bedrock/home#/modelaccess) (region: **us-east-1**)
2. Click **"Manage model access"**
3. Enable these models:
   - ✅ `Amazon Nova Sonic` (`amazon.nova-2-sonic-v1:0`)
   - ✅ `Amazon Nova Lite` (`amazon.nova-lite-v1:0`)
   - ✅ `Amazon Titan Embeddings V2 - Text`
4. Click **"Save changes"** — approval may take 1–5 minutes

5. Switch region to **ap-south-1** and repeat for:
   - ✅ `Amazon Nova Lite`
   - ✅ `Amazon Titan Embeddings V2 - Text`
   - ✅ `Anthropic Claude 3.5 Sonnet` (for knowledge distillation)

---

## PHASE 2: DynamoDB Table Setup

### Step 2.1 — Create the Transcript Table

1. Go to [AWS Console → DynamoDB → Tables](https://console.aws.amazon.com/dynamodbv2/home#tables) (region: **ap-south-1**)
2. Click **"Create table"**
3. Table name: `InDiiServe_Call_Transcript_1`
4. Partition key: `session_id` (String)
5. Table settings: **Customize settings**
6. Capacity mode: **On-demand** (pay per request — cheapest for low volume)
7. Click **"Create table"**

---

## PHASE 3: Launch EC2 Instance

### Step 3.1 — Launch the Instance

1. Go to [AWS Console → EC2 → Launch Instance](https://console.aws.amazon.com/ec2/home#LaunchInstances) (region: **ap-south-1**)
2. **Name:** `indiiserve-asha-voice-agent`
3. **AMI:** Ubuntu Server 22.04 LTS (HVM), SSD Volume Type, 64-bit (x86)
4. **Instance type:** `t3.medium` (2 vCPU, 4 GB RAM — minimum for Nova Sonic)
5. **Key pair:** Click "Create new key pair"
   - Name: `indiiserve-key`
   - Type: RSA
   - Format: `.pem`
   - Click **"Create key pair"** — **SAVE THE .pem FILE, YOU CANNOT DOWNLOAD IT AGAIN**
6. **Network settings:** 
   - VPC: Default
   - Subnet: Any public subnet
   - Auto-assign public IP: **Enable**
   - Firewall: **Create security group**
     - Name: `indiiserve-sg`
     - Inbound rules:
       - SSH: Port 22, Source: **My IP** (your current IP)
       - Custom TCP: Port 8000, Source: **Anywhere (0.0.0.0/0)** (Exotel needs this)
       - Custom TCP: Port 443, Source: **Anywhere (0.0.0.0/0)**
       - HTTP: Port 80, Source: **Anywhere (0.0.0.0/0)**
7. **Storage:** 20 GB gp3 SSD (default is fine)
8. Click **"Launch instance"**

### Step 3.2 — Note the Public IP Address

1. Go to **EC2 → Instances**
2. Wait for instance state to show **"Running"** ✅
3. Click the instance ID
4. Note down the **"Public IPv4 address"** (e.g., `13.233.45.67`) — you will need this for Exotel

---

## PHASE 4: Connect to EC2 and Setup Environment

### Step 4.1 — SSH into the Instance

**On Windows (PowerShell or Git Bash):**
```powershell
# First, fix the key file permissions (Windows)
icacls "C:\path\to\indiiserve-key.pem" /inheritance:r
icacls "C:\path\to\indiiserve-key.pem" /grant:r "$($env:USERNAME):(R)"

# Connect
ssh -i "C:\path\to\indiiserve-key.pem" ubuntu@YOUR_EC2_PUBLIC_IP
```

Replace `YOUR_EC2_PUBLIC_IP` with the IP from Step 3.2.

### Step 4.2 — Update System and Install Dependencies

```bash
# Update package list
sudo apt update && sudo apt upgrade -y

# Install Python 3.11, pip, git
sudo apt install -y python3.11 python3.11-venv python3-pip git curl

# Install system library for audioop-lts (required by audio pipeline)
sudo apt install -y build-essential python3-dev

# Verify Python version
python3.11 --version
# Should output: Python 3.11.x

# Install nginx (for HTTPS reverse proxy)
sudo apt install -y nginx

# Install certbot for SSL (required for WSS - Exotel needs HTTPS)
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot
```

---

## PHASE 5: Transfer and Setup the Application

### Step 5.1 — Upload Your Code to EC2

**Option A: Using SCP from Windows (recommended)**
```powershell
# From your local Windows machine (PowerShell)
# Compress the project first
Compress-Archive -Path "d:\InDiiServe Nova Sonic Voice Agent\InDiiServe Nova Sonic Voice Agent\*" -DestinationPath "C:\temp\indiiserve.zip"

# Upload to EC2
scp -i "C:\path\to\indiiserve-key.pem" "C:\temp\indiiserve.zip" ubuntu@YOUR_EC2_PUBLIC_IP:~/
```

**Option B: Using Git (if your code is in a private GitHub repo)**
```bash
# On the EC2 instance
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git /home/ubuntu/indiiserve
```

### Step 5.2 — Extract and Setup the Project

```bash
# On the EC2 instance
cd ~

# If using SCP upload (Option A):
sudo apt install -y unzip
unzip indiiserve.zip -d /home/ubuntu/indiiserve

# Navigate to project
cd /home/ubuntu/indiiserve

# Create Python virtual environment
python3.11 -m venv venv

# Activate it
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install all dependencies (this may take 3-5 minutes)
pip install -r requirements.txt
```

> [!NOTE]
> If `faiss-cpu` fails to install, run: `pip install faiss-cpu --no-build-isolation`

---

## PHASE 6: Configure Environment Variables

### Step 6.1 — Create the Production `.env` File

```bash
# On the EC2 instance, inside the project directory
nano /home/ubuntu/indiiserve/.env
```

Paste and fill in the following (replace ALL placeholder values):

```env
# ═══════════════════════════════════════════════════════
#  InDiiServe Asha — PRODUCTION Environment Configuration
# ═══════════════════════════════════════════════════════

# --- Simulation & Debugging (MUST BE false IN PRODUCTION) ---
DEMO_MODE=false
HOSPITAL_ID=YOUR_HOSPITAL_ID_HERE

# --- AWS Credentials (from IAM Step 1.1) ---
AWS_ACCESS_KEY_ID=YOUR_AWS_ACCESS_KEY_ID_HERE
AWS_SECRET_ACCESS_KEY=YOUR_AWS_SECRET_ACCESS_KEY_HERE
BEDROCK_REGION=us-east-1
AWS_REGION=ap-south-1

# --- Exotel Telephony (from your Exotel account) ---
EXOTEL_API_KEY=YOUR_EXOTEL_API_KEY
EXOTEL_API_TOKEN=YOUR_EXOTEL_API_TOKEN
EXOTEL_SID=YOUR_EXOTEL_ACCOUNT_SID
EXOTEL_SUBDOMAIN=api.exotel.com
EXOTEL_FROM_NUMBER=+91XXXXXXXXXX
EXOTEL_APP_ID=YOUR_APP_BAZAR_APP_ID
SIP_ENDPOINT=sip:YOUR_SIP_ENDPOINT

# --- Server Config (CRITICAL - set to your EC2 domain/IP) ---
PORT=8000
WS_PUBLIC_URL=wss://YOUR_DOMAIN_OR_IP/exotel-stream

# --- Security (generate a fresh key!) ---
# Run this command to generate: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=YOUR_FRESH_GENERATED_ENCRYPTION_KEY_HERE

# --- Database ---
DYNAMODB_TABLE_NAME=InDiiServe_Call_Transcript_1
RDS_HOSTNAME=
RDS_USERNAME=indiiserve_user
RDS_DB_NAME=indiiserve_analytics

# --- Optional: Knowledge Base RAG ---
KB_ID=
KB_REGION=ap-south-1

# --- Optional: AgentCore Memory ---
MEMORY_ID=
MEMORY_REGION=ap-south-1

# --- Optional: Google Sheets ---
GOOGLE_SHEET_ID=
```

Save and exit: `Ctrl+X` → `Y` → Enter

### Step 6.2 — Generate a Fresh Encryption Key

```bash
# On EC2, inside the project with venv active
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output and paste it as `ENCRYPTION_KEY` in the `.env` file.

---

## PHASE 7: Set Up HTTPS with Nginx (Required for WSS)

> [!IMPORTANT]
> Exotel requires **HTTPS (WSS)** endpoints. You cannot use plain HTTP/WS for production voice calls. You need either a domain name or a self-signed certificate.

### Option A: You Have a Domain Name (Recommended)

**Step 7A.1 — Point your domain to the EC2 IP**
- In your domain registrar's DNS settings, add an **A record**:
  - Name: `asha` (or `@` for root domain)
  - Value: `YOUR_EC2_PUBLIC_IP`
  - TTL: 300

**Step 7A.2 — Configure Nginx**
```bash
sudo nano /etc/nginx/sites-available/indiiserve
```

Paste:
```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
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
```

```bash
sudo ln -s /etc/nginx/sites-available/indiiserve /etc/nginx/sites-enabled/
sudo nginx -t   # Should say "syntax is ok"
sudo systemctl restart nginx

# Get SSL certificate from Let's Encrypt (free)
sudo certbot --nginx -d YOUR_DOMAIN.com
# Follow the prompts - enter email, agree to terms
# Certbot will automatically configure HTTPS
```

**Update `.env`:**
```
WS_PUBLIC_URL=wss://YOUR_DOMAIN.com/exotel-stream
```

### Option B: No Domain — Use EC2 Public IP with Self-Signed Cert

> [!WARNING]
> This works for testing but Exotel may reject self-signed certificates in production. Use only for initial testing.

```bash
# Generate self-signed certificate
sudo mkdir -p /etc/ssl/indiiserve
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/indiiserve/server.key \
    -out /etc/ssl/indiiserve/server.crt \
    -subj "/C=IN/ST=Maharashtra/L=Mumbai/O=InDiiServe/CN=YOUR_EC2_IP"

sudo nano /etc/nginx/sites-available/indiiserve
```

Paste:
```nginx
server {
    listen 443 ssl;
    server_name YOUR_EC2_PUBLIC_IP;
    
    ssl_certificate /etc/ssl/indiiserve/server.crt;
    ssl_certificate_key /etc/ssl/indiiserve/server.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
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

server {
    listen 80;
    server_name YOUR_EC2_PUBLIC_IP;
    return 301 https://$host$request_uri;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/indiiserve /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

**Update `.env`:**
```
WS_PUBLIC_URL=wss://YOUR_EC2_PUBLIC_IP/exotel-stream
```

---

## PHASE 8: Launch the Application

### Step 8.1 — Test Run First

```bash
# On EC2, activate venv and go to project directory
cd /home/ubuntu/indiiserve
source venv/bin/activate

# Set PYTHONPATH
export PYTHONPATH=/home/ubuntu/indiiserve

# Test run (Ctrl+C to stop)
python -m uvicorn src.server:app --host 127.0.0.1 --port 8000 --log-level info
```

**Expected output:**
```
[STARTUP] InDiiServe Asha Voice Agent starting...
[STARTUP] RDS initialization skipped (Mock/Offline mode).
INFO: Application startup complete.
INFO: Uvicorn running on http://127.0.0.1:8000
```

**Test the health endpoint from another terminal:**
```bash
curl http://localhost:8000/health
# Expected: {"status":"healthy","active_sessions":0,"service":"InDiiServe-Asha-Voice-Agent"}
```

**Test from the internet (from your local Windows machine):**
```bash
curl https://YOUR_DOMAIN_OR_IP/health
```

If you get a JSON response → the server is reachable. ✅

### Step 8.2 — Create a systemd Service (Run Forever, Auto-Restart)

```bash
sudo nano /etc/systemd/system/indiiserve.service
```

Paste:
```ini
[Unit]
Description=InDiiServe Asha Voice Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/indiiserve
Environment=PYTHONPATH=/home/ubuntu/indiiserve
EnvironmentFile=/home/ubuntu/indiiserve/.env
ExecStart=/home/ubuntu/indiiserve/venv/bin/uvicorn src.server:app --host 127.0.0.1 --port 8000 --workers 1 --log-level info
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=indiiserve

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable indiiserve
sudo systemctl start indiiserve

# Check status
sudo systemctl status indiiserve

# View live logs
sudo journalctl -u indiiserve -f
```

---

## PHASE 9: Configure Exotel

### Step 9.1 — Log into Exotel Dashboard

1. Go to [https://my.exotel.com](https://my.exotel.com)
2. Login with your Exotel credentials

### Step 9.2 — Create a Voice App (App Bazar → Voice Bot)

1. Go to **App Bazar → Voice Bot**
2. Click **"Create new App"**
3. App name: `InDiiServe Asha`
4. **Voicebot URL:** `https://YOUR_DOMAIN_OR_IP/incoming-call`
5. **Method:** GET
6. Save the app — note the **App ID** (use this as `EXOTEL_APP_ID` in `.env`)

### Step 9.3 — Connect the App to Your Exotel Number

1. Go to **Numbers** (your Exotel virtual number)
2. For the number you want to use (the one you'll call), click **"Configure"**
3. Under **"When a call comes in"**, select: **"App Bazar"**
4. Select your `InDiiServe Asha` app
5. Save

### Step 9.4 — Get Your Exotel API Credentials

1. Go to **Settings → API Keys**
2. Copy:
   - **API Key** → `EXOTEL_API_KEY` in `.env`
   - **API Token** → `EXOTEL_API_TOKEN` in `.env`
3. Your **Account SID** is visible in the dashboard URL or settings → `EXOTEL_SID`

### Step 9.5 — Update `.env` with Exotel Credentials

```bash
nano /home/ubuntu/indiiserve/.env
# Fill in EXOTEL_* values
# Save and exit
```

Restart the service:
```bash
sudo systemctl restart indiiserve
```

---

## PHASE 10: First Real Call Test

### Step 10.1 — Make the Call

1. Take your **mobile phone**
2. Call the **Exotel virtual number** you configured in Step 9.3
3. You should hear the greeting audio within 1–2 seconds
4. After ~3 seconds, Nova Sonic (Asha) will greet you
5. Speak naturally — try: *"Hello, I want to book an appointment"*

### Step 10.2 — Monitor Live Logs

```bash
# Watch real-time logs on EC2
sudo journalctl -u indiiserve -f
```

**Expected log sequence:**
```
Exotel client connected
WS query params - CallSid: ..., CallFrom: ***...XXXX
Exotel stream started - streamSid: ..., callSid: ...
Greeting audio sent to Exotel (11562 bytes PCM, polished)
Nova session setup complete, idle monitor started
Text output [USER]: Hello I want to book an appointment
Text output [ASSISTANT]: Hello! Welcome to InDiiServe Healthcare...
Tool called: appointmentBookingTool
Tool result received
```

### Step 10.3 — Verify Health After Call

```bash
curl https://YOUR_DOMAIN_OR_IP/health
# Should show: active_sessions: 0 (session cleaned up after hangup)
```

---

## PHASE 11: Monitoring and Maintenance

### Useful Commands

```bash
# View last 100 log lines
sudo journalctl -u indiiserve -n 100

# View logs since restart
sudo journalctl -u indiiserve --since "2026-05-12 00:00:00"

# Restart the service (e.g., after code update)
sudo systemctl restart indiiserve

# Check service status
sudo systemctl status indiiserve

# Update code from local machine (PowerShell)
scp -i "indiiserve-key.pem" "updated_file.py" ubuntu@YOUR_EC2_IP:/home/ubuntu/indiiserve/src/

# Then restart on EC2
sudo systemctl restart indiiserve

# View audit logs (security events)
cat /home/ubuntu/indiiserve/logs/security_audit.log

# View booking data
cat /home/ubuntu/indiiserve/data/bookings/hospital_bookings.csv
```

### Setting Up Log Rotation

```bash
sudo nano /etc/logrotate.d/indiiserve
```

```
/home/ubuntu/indiiserve/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        sudo systemctl kill -s USR1 indiiserve
    endscript
}
```

---

## PHASE 12: Production Hospital Data Setup

### Step 12.1 — Add Your Hospital JSON

```bash
nano /home/ubuntu/indiiserve/data/hospital_data/YOUR_HOSPITAL_ID.json
```

Example format:
```json
{
    "id": "apollo_metro",
    "name": "Apollo Metro Hospital",
    "address": "123 MG Road, Bangalore",
    "phone": "+91-80-XXXX-XXXX",
    "departments": ["Cardiology", "Orthopedics", "Pediatrics", "General Medicine"],
    "doctors": [
        {
            "name": "Dr. Priya Sharma",
            "dept": "Cardiology",
            "availability": {
                "days": ["Monday", "Wednesday", "Friday"],
                "time_slots": ["10:00 AM", "11:00 AM", "12:00 PM", "2:00 PM"]
            },
            "fee": 1500
        }
    ],
    "services": [
        {"name": "OPD Consultation", "price": 500},
        {"name": "ECG", "price": 300},
        {"name": "Blood Test (CBC)", "price": 450}
    ],
    "emergency": {
        "contact": "108",
        "instruction": "I am connecting you to our emergency team immediately. Please stay on the line."
    },
    "faq": [
        {
            "intent": "parking",
            "questions": ["parking", "car", "where to park"],
            "answer": "Parking is available in the basement. First 2 hours are free, then Rs. 30 per hour."
        }
    ]
}
```

Update `HOSPITAL_ID` in `.env` to match (e.g., `apollo_metro`), then restart:
```bash
sudo systemctl restart indiiserve
```

---

## Troubleshooting Common Issues

### Issue 1: "Exotel cannot reach your server"

```
Symptoms: Exotel keeps ringing but Asha never answers
Fix:
1. Check EC2 security group - port 443 must be open to 0.0.0.0/0
2. Check nginx: sudo systemctl status nginx
3. Test from outside: curl https://YOUR_DOMAIN/health
4. Check WS_PUBLIC_URL in .env - must use wss:// not ws://
```

### Issue 2: "Asha greets me but doesn't respond to speech"

```
Symptoms: Greeting audio plays, but no response to voice
Fix:
1. Check AWS credentials: aws sts get-caller-identity --region us-east-1
2. Verify Bedrock model access is enabled in us-east-1
3. Check logs: sudo journalctl -u indiiserve -f | grep BEDROCK
4. If "[BEDROCK] Missing or dummy credentials" appears → fix .env AWS keys
```

### Issue 3: "Service crashes after a few calls"

```
Symptoms: systemd shows "Failed" after some calls
Fix:
1. Check logs: sudo journalctl -u indiiserve -n 200 | grep ERROR
2. Look for UnboundLocalError → apply CRIT-03 fix from VULNERABILITY_REPORT.md
3. Increase RAM if needed: upgrade to t3.large
```

### Issue 4: "SSL certificate error with Exotel"

```
Symptoms: Exotel returns SSL handshake failure
Fix:
1. Use Let's Encrypt certificate (free): sudo certbot --nginx -d YOUR_DOMAIN
2. Self-signed certs are often rejected by Exotel in production
3. Verify cert: openssl s_client -connect YOUR_DOMAIN:443
```

### Issue 5: "pip install fails with faiss-cpu error"

```bash
# Try with binary wheel
pip install faiss-cpu --only-binary faiss-cpu

# Or build from source (slower, needs build-essential)
sudo apt install -y libopenblas-dev
pip install faiss-cpu --no-build-isolation
```

---

## Quick Reference: Critical `.env` Checklist Before Going Live

Before your first real patient call, verify every item below:

- [ ] `DEMO_MODE=false`
- [ ] `AWS_ACCESS_KEY_ID` — real key from IAM
- [ ] `AWS_SECRET_ACCESS_KEY` — real secret
- [ ] `BEDROCK_REGION=us-east-1` — where Nova Sonic is available
- [ ] `EXOTEL_API_KEY` — from Exotel dashboard
- [ ] `EXOTEL_API_TOKEN` — from Exotel dashboard
- [ ] `EXOTEL_SID` — your account SID
- [ ] `EXOTEL_SUBDOMAIN=api.exotel.com`
- [ ] `EXOTEL_FROM_NUMBER` — your Exotel number (+91XXXXXXXXXX)
- [ ] `EXOTEL_APP_ID` — from App Bazar after creating voice bot
- [ ] `WS_PUBLIC_URL=wss://YOUR_DOMAIN/exotel-stream` — NOT localhost
- [ ] `ENCRYPTION_KEY` — freshly generated Fernet key
- [ ] `HOSPITAL_ID` — matches your hospital JSON file name
- [ ] Bedrock model access enabled in us-east-1
- [ ] DynamoDB table `InDiiServe_Call_Transcript_1` exists in ap-south-1
- [ ] EC2 security group port 443 open to 0.0.0.0/0
- [ ] nginx configured and running with HTTPS
- [ ] systemd service `indiiserve` is active and running

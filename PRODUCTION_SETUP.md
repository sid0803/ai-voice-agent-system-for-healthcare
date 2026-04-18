# 🏗️ Production Setup Guide: InDiiServe Nova Sonic

This guide provides the step-by-step instructions required to transition your local sandbox environment into a live production-grade clinical voice infrastructure.

---

## 1. AWS Cloud Infrastructure

The InDiiServe system uses a **Sovereign Cloud Architecture**, meaning all data remains within your clinical AWS account.

### A. Amazon Bedrock (The Brain)
1. **Model Access**: Go to the Bedrock Console and ensure you have access to:
   - `Amazon Nova Sonic` (Primary Voice Intelligence)
   - `Amazon Nova Lite` (Post-Call Analytics)
   - `Amazon Titan Embeddings v2` (Knowledge Base)
2. **Knowledge Base**: Create a Bedrock Knowledge Base and upload your clinical PDFs/Docs. Note the `KB_ID`.

### B. Amazon DynamoDB (The Vault)
1. Create a table named `InDiiServe_Call_Transcripts`.
2. Partition Key: `session_id` (String).
3. This table will serve as the immutable clinical audit log.

### C. Amazon RDS (The Scientist)
1. Provision a PostgreSQL instance (t3.micro is sufficient for initial pilot).
2. Note the Hostname, Database Name, and Credentials.
3. The system will automatically initialize the schema on the first connection.

---

## 2. Exotel Telephony Setup

### A. Credentials
Log in to your Exotel Dashboard and retrieve:
- **Account SID**: Your primary account identifier.
- **API Key**: Required for authenticated requests.
- **API Token**: Required for secure WebSocket streams.

### B. WebSocket Configuration
In the Exotel numbers configuration:
1. Set the **Voice URL** to your server's endpoint: `https://your-domain.com/exotel-webhook`.
2. Ensure SSL/TLS is enabled (Exotel requires `wss://` for binary streams).

---

## 3. Environment Configuration (.env)

Map the following variables in your production `.env` file:

```env
# --- AI Intelligence ---
BEDROCK_REGION=us-east-1
KB_ID=your_bedrock_kb_id_here
ANALYTICS_MODEL_ID=amazon.nova-lite-v1:0

# --- Telephony (Exotel) ---
EXOTEL_SID=your_exotel_sid
EXOTEL_API_KEY=your_exotel_key
EXOTEL_TOKEN=your_exotel_token

# --- Analytics (RDS) ---
DATABASE_URL=postgresql://user:password@hostname:5432/dbname
RDS_ENCRYPTION_KEY=your_aes_256_key_here

# --- Sinks ---
GOOGLE_SHEET_ID=your_google_sheet_id_for_bookings
DYNAMODB_TABLE_NAME=InDiiServe_Call_Transcripts
```

---

## 4. Scaling Strategy
- **Headless Mode**: Run `src/server.py` using Gunicorn/Uvicorn with multiple workers for concurrency.
- **Horizontal Scaling**: Use an AWS Load Balancer (ALB) to distribute traffic across EC2/ECS instances.
- **Deduplication**: The system includes a built-in semantic cache (FAISS) to reduce Bedrock API costs by up to 40% for frequent queries.

---

> [!IMPORTANT]
> **Clinical Safety Note**: Before going live, perform a "Red Team" test by calling the agent and simulating medical emergencies to ensure the `handoffTool` triggers correctly for your specific facility.

---
*Doc Version: 2.1.0 | Target: Production Pilot*

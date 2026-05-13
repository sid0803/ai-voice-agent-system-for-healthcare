# 🏗️ Technical Blueprint
## InDiiServe Nova Sonic Voice Agent
**Version:** 1.0 | **Date:** 2026-05-12

---

## 1. System Architecture Overview

```
                          ┌─────────────────────────────────────┐
                          │         EXOTEL PSTN CLOUD           │
                          │  (Indian Telephone Network Gateway)  │
                          └───────────────┬─────────────────────┘
                                          │ HTTP GET /incoming-call
                                          │ (returns WebSocket URL)
                                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    AWS EC2 (t3.medium / Ubuntu 22.04)                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                 FastAPI Application (uvicorn)                │    │
│  │                     src/server.py                           │    │
│  │                                                             │    │
│  │  GET /health          ──→  Health check (AWS ALB)           │    │
│  │  GET /incoming-call   ──→  Returns WSS URL to Exotel        │    │
│  │  WSS /exotel-stream   ──→  Bidirectional audio stream       │    │
│  │  POST /outbound-call  ──→  Initiate outbound call           │    │
│  │  POST /failover       ──→  Transfer call to SIP             │    │
│  └──────────────────────────────┬──────────────────────────────┘    │
│                                 │                                    │
│         ┌───────────────────────┼──────────────────────────┐        │
│         ▼                       ▼                           ▼        │
│  ┌────────────┐       ┌──────────────────┐       ┌───────────────┐  │
│  │  Audio     │       │  Nova Sonic      │       │  Tool         │  │
│  │  Pipeline  │       │  Client          │       │  Processor    │  │
│  │            │       │  nova_client.py  │       │  tools.py     │  │
│  │ AudioHardener──────→S2SBidirectional ─────────→ hospitalInfo  │  │
│  │ AudioPolisher       StreamClient      │       │ doctorAvail   │  │
│  │ exotel_to_pcm       SessionData       │       │ appointment   │  │
│  │ pcm_to_exotel       StreamSession     │       │ triage        │  │
│  └────────────┘       └───────┬──────────┘       │ billing       │  │
│                               │                  │ emergency     │  │
│                               ▼                  └───────────────┘  │
│                    ┌──────────────────┐                             │
│                    │  AWS Bedrock     │                             │
│                    │  Nova Sonic      │                             │
│                    │  (us-east-1)     │◄── Bidirectional Stream    │
│                    │  S2S Model       │                             │
│                    └──────────────────┘                             │
│                                                                      │
│  ┌────────────────────┐  ┌───────────────┐  ┌───────────────────┐  │
│  │  AgentCore Memory  │  │  Analytics    │  │  Learning Engine  │  │
│  │  memory_manager.py │  │  rds_client   │  │  distiller.py     │  │
│  │                    │  │  processor.py │  │  FAISS Cache      │  │
│  │  AWS Bedrock Agent │  │               │  │                   │  │
│  │  Core (ap-south-1) │  │  SQLite/RDS   │  │  DynamoDB         │  │
│  └────────────────────┘  └───────────────┘  └───────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Deep-Dive

### 2.1 Entry Point: `src/server.py`

The main FastAPI application and the orchestration layer.

**Responsibilities:**
- Accept WebSocket connections from Exotel
- Manage session lifecycle (create → stream → teardown)
- Inject greeting audio before Nova Sonic is ready
- Coordinate memory retrieval and system prompt construction
- Run idle monitoring (silence detection)
- Post-call transcript storage and analytics trigger

**Key Design Decisions:**
- Uses `asyncio.ensure_future()` for fire-and-forget audio sends
- `asyncio.Lock` on `session_map` prevents concurrent cleanup bugs
- IST timezone used throughout for India compliance
- SIGTERM-aware lifespan handles graceful shutdown

---

### 2.2 AI Voice Engine: `src/nova_client.py`

**Class Hierarchy:**
```
S2SBidirectionalStreamClient  (1 instance per server)
    └── create_stream_session()
            └── StreamSession  (1 per active call)
                    └── SessionData  (internal state)
```

**Nova Sonic Protocol Flow:**
```
Client → Bedrock Stream
─────────────────────────────────────
sessionStart      (inference config, turn detection)
promptStart       (audio output config, tools list)
contentStart SYSTEM (role=SYSTEM)
textInput         (system prompt)
contentEnd
contentStart AUDIO (role=USER, interactive=true)
audioInput (loop)  ← User speaks
contentEnd         ← User stops
─────────────────────────────────────
Bedrock → Client
─────────────────────────────────────
contentStart      (type=AUDIO)
audioOutput (loop) → PCM audio chunks
contentEnd
textOutput         → Transcript
toolUse            → Tool invocation
usageEvent         → Token counts
```

**Mock Engine Fallback (`src/mock_engine.py`):**  
When AWS credentials are missing or invalid, `MockS2SStream` activates. It simulates the Bedrock stream with keyword-based responses for demos and development without cloud access.

---

### 2.3 Audio Pipeline: `src/audio_utils.py`

**Inbound (Patient → AI): `AudioHardener`**
```
Raw PCM (Exotel 8kHz 16-bit)
    → High-Pass Filter (removes traffic rumble below 150Hz)
    → Adaptive Noise Floor estimation
    → Soft-Knee Noise Gate (suppresses background chatter)
    → Automatic Gain Control (boosts distant voices up to 4x)
    → Clipped PCM → Nova Sonic
```

**Outbound (AI → Patient): `AudioPolisher`**
```
Nova Sonic PCM output
    → High-Shelf Treble Boost (improves phone speaker intelligibility)
    → Dynamic Range Compression (4:1, threshold 5000 RMS)
    → Makeup Gain (1.6x / ~4dB)
    → Clipped PCM → Exotel
```

**Note:** Exotel uses raw 8kHz 16-bit PCM — `exotel_to_pcm()` and `pcm_to_exotel()` are pass-throughs. No μ-law/A-law conversion is required.

---

### 2.4 Tool System: `src/tools.py`

**Tools Registered with Nova Sonic:**
| Tool Name | Function | Backend |
|-----------|----------|---------|
| `hospitalInfoTool` | Address, pharmacy, FAQ | Local JSON → FAISS → Bedrock KB |
| `doctorAvailabilityTool` | Doctor schedule, fees | Local roster → FAISS → Bedrock KB |
| `appointmentBookingTool` | Book appointment | Local CSV + Google Sheets |
| `clinicalTriageTool` | Symptom intake + priority | Local CSV triage journal |
| `reportStatusTool` | Lab/radiology status | Mock (HIS integration pending) |
| `handoffTool` | Emergency escalation | Triggers WebSocket close |
| `getBillingInfoTool` | Billing inquiry | Mock with tenant prices |
| `predictOTScheduleTool` | OT scheduling | Mock clinical data |

**FAISS Semantic Cache:**
```
Query → Titan Embeddings v2 (L2-normalized 1024-dim vector)
      → IndexFlatIP (cosine similarity via inner product)
      → Cache HIT if similarity ≥ 0.85
      → Cache MISS → Bedrock KB retrieve → store in FAISS
```

---

### 2.5 Multi-Tenancy: `src/integrations/`

**Tenant Data Flow:**
```
Hospital Onboarding
    → Push API (POST /hospital/data + push_token)
    → OR Scheduled Pull (HIS API URL in ingestion_config)
    → UniversalDataAdapter.normalize() (field mapping)
    → Store in tenants table (PostgreSQL/SQLite)
    → TenantManager.get_hospital_data() (in-memory cache)
    → Inject into tool handlers per call
```

**Tenant States:**
- `pending`: Rejected at WebSocket connect (code 1008)
- `sandbox`: Accepted + disclosure injected into system prompt
- `live`: Full production access

---

### 2.6 Memory System: `src/memory_manager.py`

**Per-Call Flow:**
```
Inbound call received
    → register_session(session_id, phone_number)
    → actor_id = "caller-" + last 10 digits
    → retrieve_context() [async, 2s timeout]
    → Build personalized system prompt
    
During call (each turn):
    → save_interaction(user_text, assistant_text)
    → Bedrock AgentCore stores conversational event
    
After call:
    → cleanup_session(session_id)
```

---

### 2.7 Security Layer: `src/security/audit_logger.py`

**Events Logged:**
- `SESSION_START`: New WebSocket connection
- `DATA_ACCESS`: Patient data lookups
- `TOOL_EXECUTION`: Clinical tool invocations
- `SILENCE_ESCALATION`: Automatic emergency trigger

**PII Protection:**
- Phone numbers masked in all console logs (`mask_phone()`)
- Phone numbers encrypted in WebSocket URL params (Fernet)
- Audit log written to `logs/security_audit.log` (not stdout)

---

### 2.8 Analytics Pipeline: `src/analytics/`

**Post-Call Processing:**
```
Call ends (WebSocket disconnect)
    → analytics_processor.process_call() [background task]
    → Nova Lite (amazon.nova-lite-v1:0) analyzes transcript
    → Extracts: sentiment, intent, department, outcome, urgency
    → Writes to hospital_analytics table (PostgreSQL or SQLite)
    → Dashboard reads from same table (Streamlit app.py)
```

---

## 3. Data Models

### 3.1 Database Schema (PostgreSQL/SQLite)

```sql
-- Call Analytics
hospital_analytics (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(50) UNIQUE,
    phone_number VARCHAR(20),          -- Encrypted PII
    hospital_id VARCHAR(50),
    timestamp TIMESTAMP,
    sentiment VARCHAR(20),             -- POSITIVE/NEUTRAL/NEGATIVE
    intent VARCHAR(100),               -- APPOINTMENT/EMERGENCY/INFO
    department VARCHAR(50),
    outcome VARCHAR(20),               -- BOOKED/RESOLVED/ESCALATED
    duration_seconds INT,
    transcript_summary TEXT,
    is_successful_booking BOOLEAN,
    urgency_score INT,                 -- 1-10
    is_emergency BOOLEAN,
    symptoms_list TEXT,
    follow_up_priority VARCHAR(20)     -- LOW/MEDIUM/HIGH/CRITICAL
)

-- Multi-Tenant Config
tenants (
    hospital_id VARCHAR(50) PRIMARY KEY,
    hospital_name VARCHAR(100),
    status VARCHAR(20),                -- pending/sandbox/live
    ingestion_strategy VARCHAR(20),    -- push/pull/hybrid
    push_token VARCHAR(64),            -- HMAC secret for push API
    sync_interval_mins INT,
    last_sync_at TIMESTAMP,
    hospital_data_normalized JSONB,    -- Normalized hospital data
    ingestion_config JSONB,            -- Pull URL, headers, etc.
    spreadsheet_id VARCHAR(100),
    created_at TIMESTAMP
)

-- Dashboard Users
users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE,
    password_hash VARCHAR(255),        -- bcrypt
    hospital_id VARCHAR(50),
    role VARCHAR(20),                  -- admin/staff/viewer
    is_admin BOOLEAN
)
```

### 3.2 AWS DynamoDB: Call Transcripts

```
Table: InDiiServe_Call_Transcript_1
Partition Key: session_id (String)

Item structure:
{
    "session_id": "uuid",
    "caller_phone": "encrypted_phone",
    "hospital_id": "apollo_metro",
    "timestamp": "ISO-8601",
    "call_duration": 120,
    "transcript": [
        {"role": "USER", "content": "Hello..."},
        {"role": "ASSISTANT", "content": "Hello, I'm Asha..."}
    ]
}
```

---

## 4. Infrastructure Design

### 4.1 AWS Services Used

| Service | Purpose | Region |
|---------|---------|--------|
| Bedrock Nova Sonic | Speech-to-Speech AI | us-east-1 |
| Bedrock Nova Lite | Post-call analytics | ap-south-1 |
| Bedrock Titan Embeddings v2 | FAISS query vectors | us-east-1 |
| Bedrock Knowledge Base | Hospital FAQ RAG | ap-south-1 |
| Bedrock AgentCore Memory | Patient personalization | ap-south-1 |
| EC2 (t3.medium) | Application server | ap-south-1 |
| RDS PostgreSQL | Analytics database | ap-south-1 |
| DynamoDB | Call transcript vault | ap-south-1 |
| Secrets Manager | Encryption key, API tokens | ap-south-1 |

### 4.2 Network Architecture

```
Internet → EC2 Security Group
    Inbound: 443 (HTTPS/WSS from Exotel)
    Inbound: 80 (HTTP redirect)
    Inbound: 22 (SSH from your IP only)
    Outbound: All (Bedrock, DynamoDB, RDS)

EC2 → AWS Services (via VPC endpoints for private traffic)
EC2 → Exotel API (outbound HTTPS on 443)
```

---

## 5. Future Enhancements

### Phase 2 (3–6 months)

| Feature | Description | Priority |
|---------|-------------|----------|
| **WhatsApp Integration** | Send booking confirmation via Twilio WhatsApp API | High |
| **EMR Integration** | Pull patient history from hospital EMR systems | High |
| **Real-Time Dashboard** | WebSocket-based live call monitoring | Medium |
| **Custom Voice Training** | Fine-tune Asha's voice persona | Medium |
| **IVR Fallback** | DTMF fallback for callers who cannot speak | Medium |
| **Outbound Appointment Reminders** | Proactive calls 24h before appointment | High |

### Phase 3 (6–12 months)

| Feature | Description | Priority |
|---------|-------------|----------|
| **Multi-Hospital Routing** | Single number routes to correct hospital | High |
| **Real-Time OT Scheduling** | Live OT management system integration | Medium |
| **Prescription Renewal** | Voice-based repeat prescription requests | Medium |
| **Insurance Verification** | Real-time TPA insurance eligibility check | High |
| **ML-Based Call Scoring** | Predict booking probability in real-time | Low |
| **SIP Trunking** | Direct SIP integration for hospital PBX systems | Medium |
| **FHIR R4 Compatibility** | Standard healthcare data exchange | Medium |

### Phase 4 (12+ months)

| Feature | Description |
|---------|-------------|
| **Voice Biometrics** | Patient authentication by voice print |
| **Regional Language Support** | Tamil, Telugu, Bengali, Marathi |
| **Teleconsultation Bridge** | Connect patient directly to doctor via voice |
| **Predictive No-Show Detection** | AI predicts appointment cancellations |
| **Autonomous Follow-Up Agent** | Post-discharge voice follow-up calls |

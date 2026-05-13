# InDiiServe Nova Sonic Voice Agent — Future Enhancement Roadmap

This document outlines the strategic phases and technical enhancements required to scale the **InDiiServe Nova Sonic Voice Agent** from its current hardened production state to a massive, multi-tenant, sub-300ms latency enterprise system.

---

## 1. Latency Optimization (Sub-300ms Goal)

While the current system has been optimized significantly (down from ~5s to ~650ms), physical network realities require architectural shifts to reach human-like interruption speed.

### A. Pre-Synthesis (Amazon Polly)
- **Current State:** Every generic response (e.g., "Hello, how can I help?") goes through Nova Sonic LLM + TTS (~600ms).
- **Enhancement:** Implement Amazon Polly to pre-generate hundreds of common conversational filler phrases (e.g., "Let me check that for you", "I understand").
- **Impact:** While the LLM processes the database query in the background, the pre-synthesized audio plays immediately, dropping perceived latency to **< 200ms**.

### B. Bedrock Pre-Warming
- **Current State:** The first call of the day suffers a "cold start" latency (~2-4 seconds) as Bedrock allocates compute for the Nova model.
- **Enhancement:** Implement a lightweight ping via `EventBridge` that sends a dummy token every 10 minutes to keep the Bedrock compute layer warm.
- **Impact:** Eliminates the 4-second cold-start penalty for early morning callers.

---

## 2. Scalability & Database Architecture

The current SQLite/Local FAISS architecture is perfect for a single EC2 instance, but will hit limits when traffic scales across multiple availability zones.

### A. Redis-backed Semantic Search (Replacing FAISS)
- **Current State:** In-memory FAISS vector database stored locally on the server. Does not share knowledge if the app scales to multiple servers.
- **Enhancement:** Migrate from local `faiss-cpu` to **Redis Vector Search (ElastiCache)** or **Pinecone**.
- **Impact:** All worker nodes share the same semantic cache, dramatically reducing AWS Bedrock token costs as the user base grows.

### B. Full Postgres Migration (RDS)
- **Current State:** Demo DB (`indiiserve_demo.db`) uses SQLite. Analytics uses a basic RDS connection.
- **Enhancement:** Full migration of patient data, appointment slots, and real-time operational state to a multi-AZ **Amazon Aurora Serverless v2 PostgreSQL** cluster.
- **Impact:** Guaranteed ACID compliance for appointment booking, supporting 10,000+ concurrent calls without database lock issues.

---

## 3. Advanced Agentic Capabilities

### A. Real-time Interruption Handling (Full Duplex)
- **Current State:** The system pauses listening while speaking. If the user interrupts, the AI must finish its sentence before processing the interruption.
- **Enhancement:** Leverage Bedrock's true Bidirectional streaming capabilities to implement active listening. The AI will instantly halt TTS generation if user audio energy spikes (VAD interruption).
- **Impact:** A profoundly more natural conversation flow that feels like talking to a real receptionist.

### B. Emotion & Prosody Tuning
- **Current State:** Nova Sonic speaks with a consistent, professional tone.
- **Enhancement:** Inject SSML (Speech Synthesis Markup Language) dynamically based on the detected `urgency_score`. If a patient says they are bleeding, the AI's tone shifts to urgent and comforting.
- **Impact:** Increased patient trust and higher customer satisfaction (CSAT) scores.

---

## 4. Multi-Tenant Infrastructure (SaaS)

### A. AWS ECS Fargate Migration
- **Current State:** Systemd service on a single EC2 instance.
- **Enhancement:** Containerize the application fully and deploy via AWS Elastic Container Service (ECS) Fargate behind an Application Load Balancer (ALB).
- **Impact:** Auto-scaling. The system will automatically spin up 10 servers at 9:00 AM (peak booking time) and scale down to 1 server at 3:00 AM, saving massive costs.

### B. Tenant-Specific Fine Tuning
- **Current State:** One global `SYSTEM_PROMPT` for all hospitals.
- **Enhancement:** Allow hospital admins to upload their own SOPs (Standard Operating Procedures) into an S3 bucket. A nightly job updates the Knowledge Base specifically for that tenant's phone number.
- **Impact:** Complete white-labeling capability, allowing InDiiServe to onboard hundreds of hospitals with distinct personalities and rules.

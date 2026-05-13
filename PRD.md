# 📋 Product Requirements Document (PRD)
## InDiiServe Nova Sonic Voice Agent — "Asha"
**Version:** 1.0 | **Date:** 2026-05-12 | **Status:** Active Development

---

## 1. Executive Summary

InDiiServe Nova Sonic Voice Agent (codename: **Asha**) is an AI-powered, real-time voice receptionist for Indian healthcare facilities. The system receives inbound phone calls via Exotel, streams audio bidirectionally with Amazon Bedrock Nova Sonic (speech-to-speech AI), and provides hospital services: appointment booking, doctor availability, clinical triage, lab report status, billing inquiry, and emergency escalation — all in English, Hindi, and Hinglish.

---

## 2. Problem Statement

Indian tier-2 and tier-3 hospitals face acute staff shortages. Front desk receptionists miss calls, cannot handle peak loads, and are unavailable after hours. Patients who cannot reach the hospital delay care, leading to preventable emergencies. Existing IVR systems are DTMF-based, not conversational, and fail non-English speakers.

---

## 3. Goals & Non-Goals

### Goals
- ✅ Answer 100% of inbound hospital calls with sub-2s first response
- ✅ Book appointments, check doctor schedules, relay report status via voice
- ✅ Detect clinical emergencies and immediately escalate to human staff
- ✅ Support English, Hindi, and Hinglish in a single conversation
- ✅ Recognize returning patients and personalize greetings
- ✅ Operate 24/7 without human intervention for routine queries
- ✅ Support multiple hospitals (multi-tenant SaaS model)

### Non-Goals
- ❌ Provide medical diagnoses or treatment recommendations
- ❌ Replace physicians, nurses, or clinical decision-making
- ❌ Handle outpatient billing payments end-to-end (inquiry only)
- ❌ Support video calls or multimedia interactions

---

## 4. Users & Personas

| Persona | Description | Key Need |
|---------|-------------|----------|
| **Patient Caller** | Tier-2/3 city patient, often elderly or rural | Book appointment, check report, understand cost |
| **Hospital Admin** | Clinic owner or IT admin | Configure Asha for their hospital, view analytics |
| **Emergency Caller** | Patient in medical distress | Immediate escalation to emergency desk |
| **Returning Patient** | Has called before, expects recognition | Personalized greeting, no re-introduction |
| **Non-English Speaker** | Speaks only Hindi or Hinglish | Natural, non-robotic Hindi responses |

---

## 5. Core Features (v1.0)

### F1: Real-Time Voice Call Handling
- Exotel telephony integration via WebSocket (bidirectional audio streaming)
- Sub-2s greeting audio playback before AI model connects
- 8kHz PCM audio processing with noise suppression and AGC

### F2: Speech-to-Speech AI (Nova Sonic)
- Amazon Bedrock Nova Sonic (`amazon.nova-2-sonic-v1:0`) for S2S streaming
- Simultaneous voice input processing and audio output generation
- Turn detection with configurable silence thresholds

### F3: Clinical Safety & Emergency Escalation
- Real-time keyword detection for emergency signals (chest pain, stroke, unconsciousness)
- Automatic handoff tool invocation within 1 conversational turn
- Silence monitoring: soft follow-up at 4s, emergency escalation at 9s

### F4: Appointment Booking
- Multi-step information collection (name, department, date, symptom)
- Date validation (no past dates accepted)
- Booking saved to local CSV and optionally Google Sheets
- Unique reference ID generated per booking

### F5: Doctor Availability & Hospital Information
- Tenant-specific doctor roster with schedule and fees
- FAISS semantic cache for repeated KB queries (sub-50ms cache hit)
- Bedrock Knowledge Base RAG fallback for complex queries

### F6: Clinical Triage
- Structured symptom collection without numeric pain scores
- Priority classification: CRITICAL / HIGH / NORMAL
- Triage journal written to CSV for clinical review

### F7: Multi-Language Support
- English, Hindi, Hinglish detection and response
- Dynamic language matching within same conversation

### F8: Patient Memory & Personalization
- AWS AgentCore Memory: recognizes returning callers by phone number
- Retrieves name, visit history, and preferences from previous sessions
- 2-second timeout on memory retrieval to not delay call start

### F9: Multi-Tenant Architecture
- Hospital-specific data (doctors, FAQ, branding) per `hospital_id`
- Tenant status: `pending` (rejected), `sandbox` (testing), `live` (production)
- Data sync: push API + scheduled pull from hospital HIS systems

### F10: Analytics & Dashboard
- Post-call AI analysis: sentiment, intent, urgency, outcome
- Streamlit dashboard with real-time call metrics
- PostgreSQL (AWS RDS) backend with SQLite demo fallback

---

## 6. Quality Requirements

| Requirement | Target |
|-------------|--------|
| First audio to caller | < 1 second |
| Nova Sonic first response | < 3 seconds after speech ends |
| System uptime | 99.5% monthly |
| Concurrent calls supported | 50+ (horizontal scaling) |
| Emergency detection accuracy | > 95% recall on test set |
| PII data encryption | Fernet AES-128 at rest |
| Audit trail retention | 90 days minimum |

---

## 7. Constraints

- **AWS Bedrock Nova Sonic** is only available in `us-east-1` region (as of 2026-05)
- **Exotel** is the mandatory telephony provider (Indian PSTN) — no Twilio/Vonage support
- Audio format locked to: 8kHz, 16-bit signed, mono, PCM (Exotel requirement)
- HIPAA/DPDP-aligned PII handling required (no PII in logs)
- System must degrade gracefully (Mock Engine) when AWS credentials are unavailable

---

## 8. Success Metrics (KPIs)

| Metric | Target (3 months post-launch) |
|--------|-------------------------------|
| Calls handled per day | 500+ |
| Appointments booked via AI | > 30% of all calls |
| Emergency escalation rate | < 5% (measures correct detection) |
| Average call duration | 90–180 seconds |
| Caller satisfaction (IVR rating) | > 4.0 / 5.0 |
| AI hallucination rate on doctor info | < 2% |

---

## 9. Release Milestones

| Milestone | Target Date | Status |
|-----------|-------------|--------|
| M1: Core voice loop (Exotel ↔ Nova Sonic) | Done | ✅ |
| M2: Appointment booking + local storage | Done | ✅ |
| M3: Clinical triage + emergency escalation | Done | ✅ |
| M4: Multi-tenancy + SaaS data pipeline | Done | ✅ |
| M5: AgentCore Memory + personalization | Done | ✅ |
| M6: Security hardening + bug fixes | In Progress | 🔄 |
| M7: AWS EC2 production deployment | Next | 📋 |
| M8: Load testing + monitoring setup | Planned | 📋 |

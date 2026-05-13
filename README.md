<div align="center">
  <img src="img/concept.png" alt="InDiiServe Asha Concept" width="100%" />

  <h1>🏥 InDiiServe Nova Sonic: Sovereign Healthcare Voice Agent</h1>
  
  <p>
    <b>An enterprise-grade, ultra-low latency conversational AI designed specifically for the Indian Healthcare ecosystem.</b>
  </p>

  <!-- Badges -->
  <p>
    <a href="https://github.com/InDiiServe/nova-sonic-voice/actions"><img src="https://img.shields.io/badge/build-passing-brightgreen.svg?style=flat-square" alt="Build Status"></a>
    <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square" alt="Python Version"></a>
    <a href="https://aws.amazon.com/bedrock/"><img src="https://img.shields.io/badge/AWS-Bedrock%20Nova-FF9900.svg?style=flat-square&logo=amazon-aws" alt="AWS Bedrock"></a>
    <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115-009688.svg?style=flat-square&logo=fastapi" alt="FastAPI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License"></a>
  </p>
</div>

---

## 📖 Table of Contents
- [Executive Summary](#-executive-summary)
- [System Architecture](#-system-architecture)
- [Key Features](#-key-features)
- [Quick Start Guide](#-quick-start-guide)
- [Deployment (Production)](#-deployment-production)
- [Security & Compliance](#-security--compliance)

---

## 🚀 Executive Summary

In modern Indian healthcare facilities, ranging from metropolitan multi-specialty hubs to Tier-2 nursing homes, **The Voice Channel** remains a critical bottleneck. During peak OPD hours, human receptionists face extreme cognitive load, resulting in up to **35% dropped calls**, empathy erosion, and unrecorded clinical data.

**InDiiServe Nova Sonic (Project Asha)** is a state-of-the-art sovereign AI Voice Receptionist. It is engineered to:
- Respond in under `800ms` using AWS Bedrock Nova Sonic models.
- Support native **Hinglish** and Indian medical vernacular.
- Provide end-to-end multi-tenant isolation for hospital networks.
- Act as a dynamic, empathetic agent that never misses an appointment opportunity.

---

## 🏗️ System Architecture

Our architecture is heavily optimized for ultra-low latency real-time telephony streams, utilizing AWS Bedrock's Bidirectional Streaming API.

```mermaid
graph TD
    subgraph "Telephony Edge (Exotel PSTN)"
        P[Patient Phone] -->|PSTN| EX[Exotel Gateway]
        EX -->|WebSocket (WSS)| WS[FastAPI Ingress]
    end

    subgraph "Core Agent Intelligence (AWS Bedrock)"
        WS <-->|Bidirectional Stream| NS[Bedrock Nova Sonic]
        NS -->|Semantic Cache| FAISS[(FAISS Cache)]
        NS -->|Query| TM[Tenant Manager]
    end

    subgraph "Hospital OS (Tools & Sinks)"
        NS -->|Invoke| TH[Tool Handler]
        TH -->|Universal Adapter| UA[Production HIS/CRM]
        TH -->|Failover| LS[(Local CSV Sink)]
        WS -->|Post-Call Async| AP[Data Science Processor]
        AP -->|Analytics| RDS[(AWS RDS Postgres)]
        AP -->|Encrypted Audit| DDB[(AWS DynamoDB)]
    end
```

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| ⚡ **Sub-Second Latency** | Vectorized audio filtering (`scipy`), streaming bidirectional outputs, and connection pooling achieve near human-like response times. |
| 🏥 **Multi-Tenancy** | True "Clinic-in-a-Box" design. Switch hospital personalities, rules, and APIs by dynamically passing `HOSPITAL_ID`. |
| 🩺 **Clinical Triage** | Hard-coded emergency detection overrides AI generation to instantly transfer critical patients (e.g., chest pain) to a human desk. |
| 🧠 **Persistent Memory** | Recognizes returning patients via AES-256 encrypted phone numbers, recalling historical context for a premium experience. |
| 📊 **Analytics Dashboard** | Included Streamlit application visualizes intent extraction, sentiment analysis, and ROI metrics stored in RDS. |

---

## 🛠️ Quick Start Guide

### Prerequisites
- Python 3.10+
- AWS Account (with Bedrock `amazon.nova-pro-v1:0` or `nova-lite-v1:0` access)
- Exotel Developer Account

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/InDiiServe/nova-sonic-voice.git
   cd nova-sonic-voice
   ```

2. **Set up the virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
   pip install -r requirements.txt
   ```

3. **Configure the Environment**
   Copy the example config and inject your AWS and Exotel secrets.
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Run the Development Server**
   ```bash
   uvicorn src.server:app --reload --port 8000
   ```

---

## 🚢 Deployment (Production)

This system is hardened for production deployment on **AWS EC2** or **ECS Fargate**. We provide comprehensive deployment guides.

> [!IMPORTANT]  
> Before deploying, you **must** run our internal diagnostic tool to ensure no configuration vulnerabilities exist.
> ```bash
> python check_deploy.py
> ```

For detailed instructions on configuring `systemd`, `nginx`, and SSL, please read the [PRODUCTION_SETUP.md](PRODUCTION_SETUP.md) guide.

---

## 🛡️ Security & Compliance

In healthcare, patient data sovereignty is paramount.
- **No Shared Pools:** All processing occurs within your dedicated AWS VPC. No data is used to train generic external models.
- **PII Hardening:** Phone numbers and identifiers are AES-256 encrypted before being stored in RDS or DynamoDB.
- **SSRF Protection:** External API requests made by the agent's sync engine are DNS-pinned to prevent Server-Side Request Forgery attacks.

---
<div align="center">
  <p>Made with ❤️ by the InDiiServe Engineering Team.</p>
  <img src="img/dashboard.png" alt="InDiiServe Dashboard" width="80%" />
</div>

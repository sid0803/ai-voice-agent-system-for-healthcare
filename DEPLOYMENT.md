# 🚀 Deployment & Production Hardening Guide

This document outlines the strategy for moving the **AI Voice Agent System for Healthcare** from a local development environment to a SaaS-ready AWS infrastructure.

---

## 🏗️ 1. Architecture Overview (The SaaS Stack)

For production, we recommend an **AWS Native** approach focused on low-latency and scalability.

- **FastAPI Server**: Deployed on **AWS App Runner** or **Amazon ECS (Fargate)**.
- **WebSocket Gateway**: **Amazon API Gateway** (WebSocket API) or direct Application Load Balancer (ALB).
- **Audio Intelligence**: **Amazon Bedrock** (Nova Sonic S2S).
- **State Management**: **Amazon ElastiCache (Redis)** for session persistence and rate limiting.
- **Database**: **Amazon RDS (Postgres)** for historical analytics.

---

## 📦 2. Dockerization

Build the image locally or via CI/CD (GitHub Actions):

```bash
docker build -t healthcare-ai-agent .
docker run -p 8080:8080 --env-file .env healthcare-ai-agent
```

---

## 📈 3. Scaling Strategy

### Vertical Scaling (Low-Latency)
- Use **Compute Optimized** instances for faster audio signal processing (PCM/mulaw conversion).

### Horizontal Scaling
- Use **AWS App Runner** to automatically scale based on the number of concurrent WebSocket connections.
- **Sticky Sessions**: Ensure the Load Balancer supports sticky sessions so the Telephone stream stays connected to the same container instance.

---

## 🛡️ 4. Failover & Reliability (Gap Resolution)

### I. Bedrock Resiliency
If Bedrock Nova Sonic experiences a regional outage:
1.  **Circuit Breaker**: The `FailoverHandler` in `server.py` detects the stream drop.
2.  **Audio Fallback**: Plays `assets/transfer.pcm` instantly.
3.  **Human Bridge**: Calls the `handoffTool` to connect the caller to a real clinic receptionist.

### II. Cost Optimization
- **Semantic Router**: Catch greetings and common intents before they reach the LLM.
- **PCM Cache**: Stream pre-recorded audio for fixed responses.

---

## 🔒 5. Security Checklist
- [ ] **VPC Private Subnets**: Ensure RDS and DynamoDB are not accessible from the public internet.
- [ ] **IAM Roles**: Use IAM Roles for Service Accounts (IRSA) instead of hardcoding `AWS_ACCESS_KEY`.
- [ ] **DDoS Protection**: Use **AWS Shield** and **WAF** to protect the WebSocket endpoint.

---

**Built for clinical grade scalability.**

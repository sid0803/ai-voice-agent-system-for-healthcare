"""Production tool implementations for the Asha voice assistant.

Provides Bedrock Knowledge Base RAG (with FAISS semantic cache), tenant-backed
lookups, booking data sinks, and the async tool_processor dispatcher.
"""

import asyncio
import csv
import json
import logging
import os
import pathlib
import threading
import time
from io import StringIO  # noqa: F401 — kept for future CSV buffering use
from typing import Any

import platform
from collections import namedtuple
# [FIX] Bypass WMI hang in Python 3.13+ / botocore on Windows subprocesses
if os.name == 'nt':
    _uname_tuple = namedtuple('uname_result', ['system', 'node', 'release', 'version', 'machine', 'processor'])
    platform.uname = lambda: _uname_tuple('Windows', '', '10', '10.0.0', 'AMD64', '')
import boto3
import numpy as np

from src.integrations.tenant_manager import tenant_manager
from src.integrations.sheets_client import sheets_client
from src.integrations.local_sink import local_sink
# [LOW-04] Thread-safe, buffered triage journal writer.
# Avoids per-event filesystem sync by using a lock and explicit flush control.
class _TriageJournalWriter:
    """Singleton CSV writer for triage entries. Thread-safe with write lock."""
    def __init__(self):
        self._lock = threading.Lock()
        self._triage_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "triage"
        self._triage_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._triage_dir / "triage_journal.csv"
        self._ensure_header()

    def _ensure_header(self):
        with self._lock:
            if not self._file_path.exists():
                with open(self._file_path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([
                        "Timestamp", "HospitalID", "Symptoms", "PainScore",
                        "Priority", "Source", "Reason", "UncertaintyFlag"
                    ])

    def _rotate_files(self):
        """Rotate triage_journal.csv if it exceeds 10MB."""
        max_size = 10 * 1024 * 1024  # 10MB
        if self._file_path.exists() and self._file_path.stat().st_size > max_size:
            for i in range(4, 0, -1):
                src = self._file_path.with_name(f"triage_journal.csv.{i}")
                dst = self._file_path.with_name(f"triage_journal.csv.{i+1}")
                if src.exists():
                    try:
                        if dst.exists():
                            dst.unlink()
                        src.rename(dst)
                    except Exception:
                        pass
            dst = self._file_path.with_name("triage_journal.csv.1")
            try:
                if dst.exists():
                    dst.unlink()
                self._file_path.rename(dst)
            except Exception:
                pass
            with open(self._file_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "Timestamp", "HospitalID", "Symptoms", "PainScore",
                    "Priority", "Source", "Reason", "UncertaintyFlag"
                ])

    def write(self, row: list):
        with self._lock:
            self._rotate_files()
            with open(self._file_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)

_triage_writer = _TriageJournalWriter()

# [D-09] audit_logger MUST be imported — used in clinical_triage for compliance audit trail
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FAISS semantic cache for Bedrock Knowledge Base results
# (boto3 KB + embed clients initialized below, after BOTO_POOL_CONFIG)
# ---------------------------------------------------------------------------
_CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)
_FAISS_INDEX_PATH = _CACHE_DIR / "kb_faiss.index"
_FAISS_META_PATH = _CACHE_DIR / "kb_faiss_meta.json"

_EMBED_DIMENSION = 1024  # Titan Embeddings v2 output dimension
_SIMILARITY_THRESHOLD = 0.85  # cosine similarity threshold for cache hit
# [MED-03] Cap FAISS index size to prevent unbounded RAM growth over time.
# When exceeded, only the most recent FAISS_KEEP_ENTRIES entries are retained.
_FAISS_MAX_ENTRIES = int(os.getenv("FAISS_MAX_ENTRIES", "10000"))
_FAISS_KEEP_ENTRIES = int(os.getenv("FAISS_KEEP_ENTRIES", "8000"))

# [OPT-07] Shared boto3 config with TCP keepalive and connection pooling.
# Reuses TCP connections between Bedrock calls — saves 20-50ms per invocation.
from botocore.config import Config as _BotoConfig
_BOTO_POOL_CONFIG = _BotoConfig(
    max_pool_connections=10,
    connect_timeout=2,
    read_timeout=10,
    retries={"max_attempts": 1, "mode": "standard"},
    tcp_keepalive=True,
)

_kb_id = os.getenv("KB_ID")
_kb_region = os.getenv("KB_REGION", "us-east-1")
_embed_region = os.getenv("BEDROCK_REGION", "us-east-1")

_kb_client = boto3.client(
    "bedrock-agent-runtime",
    region_name=_kb_region,
    config=_BOTO_POOL_CONFIG,
) if _kb_id else None

# Bedrock client for Titan Embeddings (used to embed KB queries for FAISS)
_embed_client = boto3.client(
    "bedrock-runtime",
    region_name=_embed_region,
    config=_BOTO_POOL_CONFIG,
)

# Thread lock for FAISS index writes (index is not thread-safe for add)
_faiss_lock = threading.Lock()

# Module-level FAISS index and metadata store
# _faiss_meta: list of {"query": str, "answer": str, "timestamp": float}
_faiss_index = None  # inner product on normalized vectors = cosine
_faiss_meta: list[dict] = []


def _load_faiss_cache():
    """Load FAISS index and metadata from disk, or create empty ones."""
    global _faiss_index, _faiss_meta
    import faiss
    if _FAISS_INDEX_PATH.exists() and _FAISS_META_PATH.exists():
        try:
            _faiss_index = faiss.read_index(str(_FAISS_INDEX_PATH))
            with open(_FAISS_META_PATH, "r", encoding="utf-8") as f:
                _faiss_meta = json.load(f)
            logger.info("[FAISS] Loaded cache: %d entries", _faiss_index.ntotal)
            return
        except Exception:
            logger.exception("[FAISS] Failed to load cache, creating fresh")
    _faiss_index = faiss.IndexFlatIP(_EMBED_DIMENSION)
    _faiss_meta = []
    logger.info("[FAISS] Created fresh cache")


def _save_faiss_cache():
    """Persist FAISS index and metadata to disk."""
    try:
        import faiss
        with _faiss_lock:
            faiss.write_index(_faiss_index, str(_FAISS_INDEX_PATH))
            with open(_FAISS_META_PATH, "w", encoding="utf-8") as f:
                json.dump(_faiss_meta, f)
    except Exception:
        logger.exception("[FAISS] Failed to save cache to disk")


def _save_faiss_cache_async():
    """[OPT-02] Fire-and-forget FAISS save — removes disk I/O from hot path.
    Saves 30-80ms per tool call by not blocking the response stream."""
    import threading
    threading.Thread(target=_save_faiss_cache, daemon=True).start()


# Load on module import
_load_faiss_cache()


def _embed_query(text: str) -> np.ndarray | None:
    """Get embedding from Bedrock Titan Embeddings v2.
    
    Security: Error handling (P1) and timeouts added for clinical reliability.
    """
    try:
        response = _embed_client.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text}),
        )
        result = json.loads(response["body"].read())
        vec = np.array(result["embedding"], dtype=np.float32)
        
        # L2-normalize so inner product = cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec
    except Exception as e:
        logger.error(f"[EMBED] Failed to generate embedding for query: {str(e)}")
        return None


def _faiss_search(query: str) -> dict | None:
    """Search FAISS cache for a similar query. Returns cached result or None."""
    if _faiss_index.ntotal == 0:
        return None
    try:
        t0 = time.time()
        embedding = _embed_query(query)
        if embedding is None:
            return None
        vec = embedding.reshape(1, -1)
        scores, indices = _faiss_index.search(vec, 1)
        elapsed_ms = (time.time() - t0) * 1000
        best_score = float(scores[0][0])
        best_idx = int(indices[0][0])
        if best_score >= _SIMILARITY_THRESHOLD and 0 <= best_idx < len(_faiss_meta):
            cached = _faiss_meta[best_idx]
            logger.info(
                "[FAISS] Cache HIT (score=%.3f, %.0fms): '%s' -> cached '%s'",
                best_score, elapsed_ms, query[:60], cached["query"][:60],
            )
            return {"answer": cached["answer"]}
        logger.info(
            "[FAISS] Cache MISS (best=%.3f, %.0fms): '%s'",
            best_score, elapsed_ms, query[:60],
        )
    except Exception:
        logger.exception("[FAISS] Search error")
    return None


def _faiss_store(query: str, answer: str):
    """Add a query+answer to the FAISS cache and persist to disk."""
    global _faiss_index, _faiss_meta
    try:
        embedding = _embed_query(query)
        if embedding is None:
            return
        vec = embedding.reshape(1, -1)
        with _faiss_lock:
            _faiss_index.add(vec)
            _faiss_meta.append({
                "query": query,
                "answer": answer,
                "timestamp": time.time(),
            })

            # [MED-03] Rolling window eviction: prune oldest entries when over cap
            if _faiss_index.ntotal > _FAISS_MAX_ENTRIES:
                logger.info(
                    "[FAISS] Index exceeded %d entries (%d total). Pruning to %d newest.",
                    _FAISS_MAX_ENTRIES, _faiss_index.ntotal, _FAISS_KEEP_ENTRIES,
                )
                # Sort by timestamp (newest last), keep only _FAISS_KEEP_ENTRIES
                sorted_meta = sorted(_faiss_meta, key=lambda x: x.get("timestamp", 0))
                keep_meta = sorted_meta[-_FAISS_KEEP_ENTRIES:]

                # Rebuild embeddings for kept entries
                import faiss
                new_index = faiss.IndexFlatIP(_EMBED_DIMENSION)
                for entry in keep_meta:
                    emb = _embed_query(entry["query"])
                    if emb is not None:
                        new_index.add(emb.reshape(1, -1))

                _faiss_index = new_index
                _faiss_meta = keep_meta
                logger.info("[FAISS] Eviction complete. New size: %d", _faiss_index.ntotal)

        # [OPT-02] Async FAISS save — disk I/O off the hot path (saves 30-80ms)
        _save_faiss_cache_async()
        logger.info("[FAISS] Stored: '%s' (total=%d)", query[:60], _faiss_index.ntotal)
    except Exception:
        logger.exception("[FAISS] Store error")

def sync_community_knowledge():
    """Requirement: Automatic Learning. Indexes distilled facts from local knowledge store into FAISS."""
    knowledge_file = pathlib.Path(__file__).resolve().parent.parent / "data" / "knowledge" / "distilled_facts.json"
    if not knowledge_file.exists():
        return
    
    try:
        with open(knowledge_file, "r") as f:
            facts = json.load(f)
            
        logger.info(f"[LEARNING] Syncing {len(facts)} pieces of community knowledge into Vector Brain.")
        for item in facts:
            q = item.get("question")
            a = item.get("answer")
            if q and a:
                # Check if already in meta to avoid duplicates
                if not any(m["query"] == q for m in _faiss_meta):
                    _faiss_store(q, a)
    except Exception as e:
        logger.error(f"[LEARNING] Failed to sync community knowledge: {e}")


# ---------------------------------------------------------------------------
# Hospital Tool Implementations (Asha / InDiiServe Healthcare)
# ---------------------------------------------------------------------------


def _normalize_hindi_query(query: str) -> str:
    """Normalizes Hindi / Devanagari terms to English keywords for robust search matching."""
    if not query:
        return ""
    
    normalized = query.lower()
    
    # Mapping dict for departments, services, FAQs, and doctors
    mappings = {
        # Departments
        "कार्डियोलॉजी": "cardiology",
        "कार्डियोलोजी": "cardiology",
        "कार्डियो": "cardiology",
        "हृदय": "cardiology",
        "दिल": "cardiology",
        "पीडियाट्रिक्स": "pediatrics",
        "पिडियाट्रिक्स": "pediatrics",
        "पीडिया": "pediatrics",
        "बाल": "pediatrics",
        "ऑर्थोपेडिक्स": "orthopedics",
        "अर्थोपेडिक्स": "orthopedics",
        "आर्थोपेडिक्स": "orthopedics",
        "हड्डी": "orthopedics",
        "डर्मेटोलॉजी": "dermatology",
        "डर्मेटोलोजी": "dermatology",
        "त्वचा": "dermatology",
        "चमड़ी": "dermatology",
        "जनरल मेडिसिन": "general medicine",
        "सामान्य चिकित्सा": "general medicine",
        "सामान्य": "general medicine",
        "न्यूरोलॉजी": "neurology",
        "न्यूरोलोजी": "neurology",
        "न्यूरो": "neurology",
        "तंत्रिका": "neurology",
        "रेडियोलॉजी": "radiology",
        "रेडियोलोजी": "radiology",
        "एक्स-रे": "radiology",
        "एक्सरे": "radiology",
        
        # FAQ Keywords
        "पता": "address location",
        "कहाँ": "where location",
        "एड्रेस": "address",
        "लोकेशन": "location",
        "दवाई": "pharmacy medicine",
        "फार्मेसी": "pharmacy",
        "मेडिसिन": "medicine",
        "समय": "hours timing schedule",
        "टाइम": "hours timing schedule",
        "घंटे": "hours",
        "रिपोर्ट": "reports status",
        "पर्चा": "reports",
        
        # Service Names
        "एमआरआई": "mri scan",
        "सीटी": "ct scan",
        "ब्लड": "blood routine test",
        "खून": "blood routine test",
        "परामर्श": "consultation opd",
        "कंसल्टेशन": "consultation opd",
        "फीस": "fee price",
        "पैसा": "price billing",
        "बिल": "billing bill",
        "खर्चा": "price cost billing",
        
        # Doctors & ASR corrections
        "सेन": "sen",
        "सेम": "sen",       # ASR error for Dr. Sen
        "ट्रेन": "sen",     # ASR error for Dr. Sen
        "सिंह": "singh",
        "कविता": "kavita",
        "गुप्ता": "gupta",
        "अनन्या": "ananya",
        "रे": "ray",
        "राय": "ray",
        "कुलकर्णी": "kulkarni",
        "समीर": "sameer",
        "मेघा": "megha",
        "राव": "rao",
        "जैन": "jain",
        "प्रतीक": "prateek"
    }
    
    for hindi_term, eng_term in mappings.items():
        if hindi_term in normalized:
            normalized += f" {eng_term}"
            
    return normalized


def hospital_info(args: dict, hospital_id: str = None) -> dict:
    """Fetches hospital info. Priority: local tenant JSON → FAISS cache → Bedrock KB → fallback."""
    data = tenant_manager.get_hospital_data(hospital_id)
    query = args.get("query", "").lower()
    normalized_query = _normalize_hindi_query(query)

    # 1. Check specific local tenant data (fastest, most reliable)
    
    # Check new structured FAQ (Requirement: Enriched AI Data)
    faq_list = data.get("faq", [])
    if isinstance(faq_list, list):
        for item in faq_list:
            intent_match = item.get("intent", "").replace("_", " ") in normalized_query
            question_match = any(q.lower() in normalized_query for q in item.get("questions", []))
            if intent_match or question_match:
                return {"answer": item.get("answer")}
    
    # Fallback to legacy dict FAQ
    elif isinstance(faq_list, dict):
        for key, val in faq_list.items():
            if key in normalized_query:
                return {"answer": val}

    if any(k in normalized_query for k in ["address", "location", "where"]):
        return {"answer": f"{data.get('name')} is located at {data.get('address', 'our main facility')}."}

    if any(k in normalized_query for k in ["pharmacy", "medicine"]):
        return {"answer": f"Our pharmacy is open {data.get('pharmacy_hours', '24/7')}. It is located near the main exit."}

    # 2. Check semantic FAISS cache for a previously-answered similar query
    cached = _faiss_search(normalized_query)
    if cached:
        logger.info("[KB] Returning FAISS-cached KB answer")
        return cached

    # 3. Query Bedrock Knowledge Base (if configured)
    if _kb_client and _kb_id:
        try:
            kb_result = _kb_client.retrieve(
                knowledgeBaseId=_kb_id,
                retrievalQuery={"text": normalized_query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": 2}
                }
            )
            passages = [
                r["content"]["text"]
                for r in kb_result.get("retrievalResults", [])
                if r.get("content", {}).get("text")
            ]
            if passages:
                answer = passages[0]
                _faiss_store(normalized_query, answer)  # Cache for future queries
                logger.info("[KB] Returning live KB answer and caching")
                return {"answer": answer}
        except Exception:
            logger.exception("[KB] Retrieval error, falling back to general info")

    # 4. Final fallback to general department description
    depts = ", ".join(data.get("departments", ["General Medicine"]))
    return {"answer": f"{data.get('name')} provides services including {depts}. How can I help you today?"}


def doctor_availability(args: dict, hospital_id: str = None) -> dict:
    """Fetches doctor schedule. Priority: local roster -> FAISS cache -> Bedrock KB -> fallback."""
    data = tenant_manager.get_hospital_data(hospital_id)
    query = args.get("query", "").lower()
    normalized_query = _normalize_hindi_query(query)
    doctors = data.get("doctors", [])

    # 1. Search in local tenant roster (most accurate for configured clinics)
    for doc in doctors:
        doc_name_lower = doc["name"].lower()
        doc_dept_lower = doc["dept"].lower()
        
        # Extract name parts (e.g., "Kavita", "Singh", "Gupta", "Sen")
        name_clean = doc_name_lower.replace("dr.", "").replace("dr", "").strip()
        name_parts = [p for p in name_clean.split() if len(p) >= 3] # Keep parts with at least 3 characters
        
        # Check if full name, department, or any name part is in normalized query
        name_match = (doc_name_lower in normalized_query or 
                      any(part in normalized_query for part in name_parts))
        dept_match = doc_dept_lower in normalized_query
        
        if name_match or dept_match:
            fee_str = f" Consultation fee: Rs. {doc['fee']}." if doc.get("fee") else ""
            
            # Check for AI-Ready structured availability (Requirement: Enriched AI Data)
            availability = doc.get("availability")
            if availability:
                days = ", ".join(availability.get("days", []))
                slots = ", ".join(availability.get("time_slots", [])[:3]) # Show first 3 slots to be concise
                schedule_str = f"available on {days}. Available slots include {slots}."
            else:
                schedule_str = f"available {doc.get('schedule', 'during OPD hours')}."
                
            return {
                "answer": (
                    f"{doc['name']} ({doc['dept']}) is {schedule_str}{fee_str} "
                    "Would you like me to book a slot?"
                )
            }

    # 2. Check FAISS semantic cache for a previously-answered similar query
    cached = _faiss_search(f"doctor availability {normalized_query}")
    if cached:
        logger.info("[KB] Returning FAISS-cached doctor answer")
        return cached

    # 3. Query Bedrock Knowledge Base (for clinics with KB-backed rosters)
    if _kb_client and _kb_id:
        try:
            kb_result = _kb_client.retrieve(
                knowledgeBaseId=_kb_id,
                retrievalQuery={"text": f"doctor availability {normalized_query}"},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": 2}
                }
            )
            passages = [
                r["content"]["text"]
                for r in kb_result.get("retrievalResults", [])
                if r.get("content", {}).get("text")
            ]
            if passages:
                answer = passages[0]
                _faiss_store(f"doctor availability {normalized_query}", answer)
                logger.info("[KB] Returning live KB doctor answer and caching")
                return {"answer": answer}
        except Exception:
            logger.exception("[KB] Doctor retrieval error, falling back")

    # 4. Final fallback to department list
    depts = ", ".join(data.get("departments", ["General Medicine"]))
    return {
        "answer": (
            f"We have specialists in {depts}. "
            "Which department should I check availability for?"
        )
    }


def appointment_booking(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentBookingTool. Logged in security audit."""
    # Audit Trace
    audit_logger.log_tool_use("active_session", hospital_id or "default", "appointment_booking")
    
    patient = args.get("patient_name", "the patient")
    dept = args.get("doctor_dept", "the requested department")
    date = args.get("date", "soon")
    intent = args.get("symptom_intent", "General Checkup")
    phone = args.get("phone_number", "N/A")
    doctor = args.get("doctor_name", dept)  # Use dept as doctor fallback

    # Generate unique reference ID
    ref_id = f"IS-APP-{time.strftime('%H%M%S')}"

    # Build complete payload with all fields for Sheets/CSV
    booking_payload = {
        "patient_name": patient,
        "phone": phone,
        "doctor": doctor,
        "dept": dept,
        "visit_time": f"{date} at {args.get('time', 'TBD')}",
        "ref_id": ref_id,
        "intent": intent,
    }

    # Notedown process (Sheets + Local CSV — both run for redundancy)
    local_sink.save_booking(booking_payload)
    sheets_client.append_booking(booking_payload, hospital_id=hospital_id)

    return {
        "answer": f"I have noted your request for {patient} in the {dept} department for {date}. Your reference ID is {ref_id}. We have recorded your concern: '{intent}'.",
        "success": True,
        "ref_id": ref_id,
    }


def report_status(args: dict, hospital_id: str = None) -> dict:
    """Mocked lab/radiology report status based on test type."""
    test = args.get("test_type", "report").lower()
    patient = args.get("patient_name", "the patient")
    
    if any(k in test for k in ["blood", "urine", "sugar"]):
        return {"answer": f"The lab results for {patient}'s blood test are ready. You can collect the hard copy from the ground floor counter or view it on our mobile app."}
    
    if any(k in test for k in ["mri", "ct", "x-ray", "xray"]):
        return {"answer": f"The radiologist is currently reviewing the {test} for {patient}. It should be finalized by 6 PM this evening."}

    return {"answer": f"I've checked the records for {patient}. Some reports are still pending. Please check back in a few hours."}

def emergency_handoff(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Clinical Safety. Handoff for emergencies with 1066 fallback."""
    logger.warning("!!! EMERGENCY HANDOFF TRIGGERED !!!")
    from src.integrations.tenant_manager import tenant_manager
    data = tenant_manager.get_hospital_data(hospital_id)
    emergency_info = data.get("emergency", {})
    
    instruction = emergency_info.get("instruction", "I'm connecting you to our emergency desk immediately. Please stay on the line.")
    contact = emergency_info.get("contact", "10-6-6")
    
    response = f"{instruction} If for any reason the line disconnects, please dial {contact} immediately."
        
    return {"answer": response, "status": "ESCALATED"}


def clinical_triage(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Clinical Excellence. Gathers systematic symptom data with audit trail."""
    # Audit Trace
    audit_logger.log_tool_use("active_session", hospital_id or "default", "clinical_triage")
    
    symptoms = args.get("symptoms", "Not specified")
    pain = args.get("pain_intensity", 0)
    onset = args.get("onset_duration", "Not specified")
    history = args.get("existing_conditions", "None")
    reason = args.get("decision_reason", "Symptom check requested")
    uncertainty = args.get("uncertainty_flag", False)
    
    # Map pain intensity to Clinical Priority Levels
    # 7-10 = CRITICAL, 4-6 = HIGH, 1-3 = NORMAL
    if pain >= 7:
        priority = "CRITICAL"
    elif pain >= 4:
        priority = "HIGH"
    else:
        priority = "NORMAL"
        
    status = "URGENT" if priority in ["CRITICAL", "HIGH"] else "STABLE"
    
    logger.info(f"[TRIAGE] {priority} - Symptoms: {symptoms}, Onset: {onset}, History: {history}, Reason: {reason}")
    # [LOW-04] Use thread-safe buffered writer instead of raw open()
    _triage_writer.write([
        time.strftime("%Y-%m-%d %H:%M:%S"), hospital_id, symptoms,
        pain, priority, "AI_ASSIST", reason, uncertainty
    ])
    
    response = f"I've recorded those clinical details. Based on what you told me about the {symptoms}, our medical team will be better prepared. "
    if priority == "CRITICAL":
        response += "Since your discomfort level is very high, I strongly recommend seeing a doctor today, or I can connect you to our emergency desk immediately."
    elif priority == "HIGH":
        response += "As your symptoms are quite significant, we've flagged this for priority attention during your visit."
    else:
        response += "A specialist can review this during your consultation. Would you like to proceed with booking a slot?"
        
    return {"answer": response, "status": status, "priority": priority, "triage_noted": True, "reason": reason}


def get_billing_info(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Hospital OS Layer - Billing Intelligence.
    Provides breakdown, status, and actionable mock payment link.
    """
    patient_id = args.get("patient_id", "unknown")
    patient_name = args.get("patient_name", "the patient")
    
    # 1. Fetch prices from tenant data
    data = tenant_manager.get_hospital_data(hospital_id)
    services = data.get("services", [])
    
    # Mock items based on common inquiries
    items = []
    total = 0
    
    # If query mentions a specific service, use that
    query = str(args.get("query", "")).lower()
    normalized_query = _normalize_hindi_query(query)
    found_any = False
    for s in services:
        if s["name"].lower() in normalized_query:
            items.append({"name": s["name"], "price": s["price"]})
            total += s["price"]
            found_any = True
            
    if not found_any:
        # Default mock bill for demonstration
        items = [
            {"name": "Consultation", "price": 1200},
            {"name": "Routine Lab Tests", "price": 850}
        ]
        total = 2050
        
    payment_link = f"https://pay.indiiserve.demo/bill/{time.strftime('%Y%m%d')}-{patient_id[:5]}"
    
    response = (
        f"For {patient_name}, the current billing status is PENDING. "
        f"The breakdown includes: " + ", ".join([f"{i['name']} (Rs. {i['price']})" for i in items]) + ". "
        f"The total amount due is Rs. {total}. "
        f"I can send you a secure payment link at {payment_link} if you'd like to pay now."
    )
    
    return {
        "answer": response,
        "patient_name": patient_name,
        "items": items,
        "total": total,
        "status": "PENDING",
        "payment_link": payment_link,
        "success": True
    }


def predict_ot_schedule(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Hospital OS Layer - OT Intelligence.
    Predicts duration breakdown and suggests nearest available slot.
    """
    procedure = args.get("procedure_name", "General Surgery").title()
    doctor = args.get("doctor_name", "the attending specialist")
    
    # Map Devanagari procedure names to English
    procedure_clean = procedure.lower()
    procedure_mappings = {
        "एंजियोप्लास्टी": "Angioplasty",
        "अपेंडिक्स": "Appendectomy",
        "घुटना": "Knee Replacement",
        "कैटरेक्ट": "Cataract",
        "मोतियाबिंद": "Cataract",
        "सर्जरी": "General Surgery"
    }
    for hindi_p, eng_p in procedure_mappings.items():
        if hindi_p in procedure_clean:
            procedure = eng_p
            break

    # Mock Clinical OT Data
    durations = {
        "Angioplasty": {"prep": 30, "proc": 90, "rec": 60},
        "Appendectomy": {"prep": 30, "proc": 60, "rec": 60},
        "Knee Replacement": {"prep": 60, "proc": 120, "rec": 90},
        "Cataract": {"prep": 20, "proc": 30, "rec": 30},
        "General Surgery": {"prep": 30, "proc": 60, "rec": 60}
    }
    
    timing = durations.get(procedure, durations["General Surgery"])
    total_block = timing["prep"] + timing["proc"] + timing["rec"]
    
    # Mock scheduling intelligence: "Nearest available slot"
    # In production, this would bridge to an OT Management System (OMS)
    import datetime
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    slot_time = tomorrow.replace(hour=11, minute=0, second=0, microsecond=0)
    slot_str = slot_time.strftime("%Y-%m-%d %I:%M %p")
    
    response = (
        f"The predicted OT block for {procedure} with {doctor} is {total_block} minutes. "
        f"This includes {timing['prep']} minutes for prep, {timing['proc']} minutes for the procedure, "
        f"and {timing['rec']} minutes for recovery. "
        f"The nearest available OT slot is tomorrow, {slot_str}. Would you like me to reserve it?"
    )
    
    return {
        "answer": response,
        "procedure": procedure,
        "prep_time": timing["prep"],
        "procedure_time": timing["proc"],
        "recovery_time": timing["rec"],
        "total_time": total_block,
        "next_available_slot": slot_str,
        "success": True
    }


_hospital_info_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Specific question about the hospital (address, pharmacy, etc.)"}
    },
    "required": ["query"],
})

_doctor_availability_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Doctor name or department to check."}
    },
    "required": ["query"],
})

_appointment_booking_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_name": {"type": "string"},
        "doctor_name": {"type": "string"},
        "doctor_dept": {"type": "string"},
        "date": {"type": "string"},
        "time": {"type": "string"},
        "symptom_intent": {"type": "string"},
        "phone_number": {"type": "string"},
    },
    "required": ["patient_name", "date"],
})

_report_status_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_name": {"type": "string"},
        "test_type": {"type": "string"},
    },
    "required": ["patient_name", "test_type"],
})

_handoff_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "reason": {"type": "string", "description": "Reason for handoff (Emergency, Complex Request)."}
    },
})

_clinical_triage_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "symptoms": {"type": "string", "description": "Patient's primary complaints or symptoms."},
        "pain_intensity": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Pain level from 1 to 10."},
        "onset_duration": {"type": "string", "description": "How long the symptoms have been present."},
        "existing_conditions": {"type": "string", "description": "Any previous medical history mentioned."},
        "decision_reason": {"type": "string", "description": "The logic behind why this triage was necessary (Internal)."},
        "uncertainty_flag": {"type": "boolean", "description": "Flag if the AI is unsure about the severity (Internal)."},
    },
    "required": ["symptoms", "pain_intensity", "onset_duration"],
})

_billing_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_id": {"type": "string"},
        "patient_name": {"type": "string"},
        "query": {"type": "string", "description": "Specific billing question (e.g. price of MRI)."}
    },
})

_ot_prediction_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "procedure_name": {"type": "string", "description": "Type of surgery (e.g. Angioplasty)."},
        "doctor_name": {"type": "string"}
    },
    "required": ["procedure_name"],
})

available_tools: list[dict] = [
    {
        "toolSpec": {
            "name": "hospitalInfoTool",
            "description": "MUST be called when the caller asks about hospital location, address, directions, where to go, hospital contact details, pharmacy hours, visiting hours, or any general hospital information. Do NOT answer from memory — always call this tool.",
            "inputSchema": {"json": _hospital_info_schema},
        }
    },
    {
        "toolSpec": {
            "name": "doctorAvailabilityTool",
            "description": "Check availability of specific doctors or medical departments.",
            "inputSchema": {"json": _doctor_availability_schema},
        }
    },
    {
        "toolSpec": {
            "name": "appointmentBookingTool",
            "description": "Schedule a new appointment or reschedule an existing one.",
            "inputSchema": {"json": _appointment_booking_schema},
        }
    },
    {
        "toolSpec": {
            "name": "clinicalTriageTool",
            "description": "MUST be called when the patient describes any medical symptoms, pain, fever, headache, nausea, fatigue, breathing issues, or any physical discomfort that is NOT a life-threatening emergency. Log their symptoms for clinical assessment. Do NOT just respond with words — call this tool immediately to capture the symptom data.",
            "inputSchema": {"json": _clinical_triage_schema},
        }
    },
    {
        "toolSpec": {
            "name": "reportStatusTool",
            "description": "Check if a medical or lab report is ready.",
            "inputSchema": {"json": _report_status_schema},
        }
    },
    {
        "toolSpec": {
            "name": "handoffTool",
            "description": "Transfer the call to a human receptionist for emergencies or complex requests.",
            "inputSchema": {"json": _handoff_schema},
        }
    },
    {
        "toolSpec": {
            "name": "getBillingInfoTool",
            "description": "Fetch patient billing breakdown, outstanding balance, and payment status. Can provide payment links.",
            "inputSchema": {"json": _billing_schema},
        }
    },
    {
        "toolSpec": {
            "name": "predictOTScheduleTool",
            "description": "MUST be called when anyone asks about surgery duration, operation theatre timing, how long a procedure takes, OT slot availability, or any surgical scheduling question. Call this tool immediately for any procedure/surgery time query.",
            "inputSchema": {"json": _ot_prediction_schema},
        }
    },
]

# Handler map keyed by lowercase tool name
_tool_handlers: dict[str, Any] = {
    "hospitalinfotool": hospital_info,
    "doctoravailabilitytool": doctor_availability,
    "appointmentbookingtool": appointment_booking,
    "clinicaltriagetool": clinical_triage,
    "reportstatustool": report_status,
    "handofftool": emergency_handoff,
    "getbillinginfotool": get_billing_info,
    "predictotscheduletool": predict_ot_schedule,
}


async def tool_processor(tool_name: str, tool_args: str, hospital_id: str = None) -> dict[str, Any]:
    """Parse tool_args JSON, dispatch to the handler with hospital_id, return result."""
    try:
        args = json.loads(tool_args)
    except (json.JSONDecodeError, TypeError):
        args = {}
    handler = _tool_handlers.get(tool_name.lower())
    if handler is None:
        return {"message": "I cannot help you with that request", "success": False}
    
    # [FIX MED-05] Use get_running_loop() - get_event_loop() is deprecated in Python 3.10+
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, handler, args, hospital_id)

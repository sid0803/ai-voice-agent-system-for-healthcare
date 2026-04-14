"""Tool implementations for Nova Sonic voice assistant.

Provides SerpAPI search (with FAISS vector cache), Bedrock Knowledge Base
RAG search, tool specifications, and the async tool_processor dispatcher.
"""

import asyncio
import json
import logging
import os
import pathlib
import pickle
import threading
import time
from typing import Any

import aiohttp
import boto3
import numpy as np
import faiss
import requests as http_requests
from serpapi import GoogleSearch

from src.integrations.tenant_manager import tenant_manager
from src.integrations.sheets_client import sheets_client
from src.integrations.local_sink import local_sink

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-initialize KB client (reuse TCP connection pool across calls)
# ---------------------------------------------------------------------------
_kb_id = os.getenv("KB_ID")
_default_region = os.getenv("AWS_REGION", "ap-south-1")
_kb_region = os.getenv("KB_REGION", _default_region)
_kb_client = boto3.client("bedrock-agent-runtime", region_name=_kb_region) if _kb_id else None

# ---------------------------------------------------------------------------
# FAISS vector cache for SerpAPI queries (shared across all calls)
# ---------------------------------------------------------------------------
_CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)
_FAISS_INDEX_PATH = _CACHE_DIR / "serp_faiss.index"
_FAISS_META_PATH = _CACHE_DIR / "serp_faiss_meta.pkl"

_EMBED_DIMENSION = 1024  # Titan Embeddings v2 output dimension
_SIMILARITY_THRESHOLD = 0.85  # cosine similarity threshold for cache hit

# Bedrock client for Titan Embeddings (us-east-1 where Titan is available)
_embed_region = os.getenv("BEDROCK_REGION", "us-east-1")
_embed_client = boto3.client("bedrock-runtime", region_name=_embed_region)

# Thread lock for FAISS index writes (index is not thread-safe for add)
_faiss_lock = threading.Lock()

# Module-level FAISS index and metadata store
# _faiss_meta: list of {"query": str, "answer": str, "timestamp": float}
_faiss_index: faiss.IndexFlatIP = None  # inner product on normalized vectors = cosine
_faiss_meta: list[dict] = []


def _load_faiss_cache():
    """Load FAISS index and metadata from disk, or create empty ones."""
    global _faiss_index, _faiss_meta
    if _FAISS_INDEX_PATH.exists() and _FAISS_META_PATH.exists():
        try:
            _faiss_index = faiss.read_index(str(_FAISS_INDEX_PATH))
            with open(_FAISS_META_PATH, "rb") as f:
                _faiss_meta = pickle.load(f)
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
        faiss.write_index(_faiss_index, str(_FAISS_INDEX_PATH))
        with open(_FAISS_META_PATH, "wb") as f:
            pickle.dump(_faiss_meta, f)
    except Exception:
        logger.exception("[FAISS] Failed to save cache to disk")


# Load on module import
_load_faiss_cache()


def _embed_query(text: str) -> np.ndarray:
    """Get embedding from Bedrock Titan Embeddings v2."""
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


def _faiss_search(query: str) -> dict | None:
    """Search FAISS cache for a similar query. Returns cached result or None."""
    if _faiss_index.ntotal == 0:
        return None
    try:
        t0 = time.time()
        vec = _embed_query(query).reshape(1, -1)
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
    try:
        vec = _embed_query(query).reshape(1, -1)
        with _faiss_lock:
            _faiss_index.add(vec)
            _faiss_meta.append({
                "query": query,
                "answer": answer,
                "timestamp": time.time(),
            })
            _save_faiss_cache()
        logger.info("[FAISS] Stored: '%s' (total=%d)", query[:60], _faiss_index.ntotal)
    except Exception:
        logger.exception("[FAISS] Store error")


# ---------------------------------------------------------------------------
# Hospital Tool Implementations (Asha / InDiiServe Healthcare)
# ---------------------------------------------------------------------------


def hospital_info(args: dict) -> dict:
    """Requirement No 1: Fetches hospital info from the current tenant's database."""
    data = tenant_manager.get_hospital_data()
    query = args.get("query", "").lower()
    
    # Check FAQ/Info from dynamic tenant data
    faq = data.get("faq", {})
    if any(k in query for k in ["address", "location", "where"]):
        return {"answer": f"{data.get('name')} is located at {data.get('address', 'our main facility')}."}
    
    if any(k in query for k in ["pharmacy", "medicine"]):
        return {"answer": f"Our pharmacy is open {data.get('pharmacy_hours', 'during OPD hours')}. It is located on the ground floor."}
        
    for key, val in faq.items():
        if key in query:
            return {"answer": val}

    # Fallback to general description
    depts = ", ".join(data.get("departments", ["General Medicine"]))
    return {"answer": f"{data.get('name')} provides various services including {depts}. How can I help you today?"}


def doctor_availability(args: dict) -> dict:
    """Requirement No 1 & No 2: Fetches doctor availability from dynamic tenant data."""
    data = tenant_manager.get_hospital_data()
    query = args.get("query", "").lower()
    doctors = data.get("doctors", [])
    
    # Search for specific doctor or department in tenant data
    for doc in doctors:
        if doc["name"].lower() in query or doc["dept"].lower() in query:
            return {"answer": f"{doc['name']} ({doc['dept']}) is available {doc['schedule']}. Would you like me to book a slot?"}

    return {"answer": f"I've checked our current schedule. We have specialists in {', '.join(data.get('departments', []))}. Which department should I check for you?"}


def appointment_booking(args: dict) -> dict:
    """Requirement No 2: Saves booking intent to Google Sheets and local CSV."""
    patient = args.get("patient_name", "the patient")
    dept = args.get("doctor_dept", "the requested department")
    date = args.get("date", "soon")
    intent = args.get("symptom_intent", "General Checkup")
    
    # Generate reference ID
    ref_id = f"IS-APP-{time.strftime('%H%M%S')}"
    
    booking_payload = {
        "patient_name": patient,
        "dept": dept,
        "visit_time": f"{date} at {args.get('time', 'TBD')}",
        "ref_id": ref_id,
        "intent": intent
    }

    # Notedown process (Sheets + Local CSV)
    local_sink.save_booking(booking_payload)
    sheets_client.append_booking(booking_payload)
    
    return {
        "answer": f"I have noted your request for {patient} in the {dept} department for {date}. Your temporary Reference ID is {ref_id}. Asha has recorded your needs: '{intent}'.",
        "success": True,
        "ref_id": ref_id
    }


def report_status(args: dict) -> dict:
    """Mocked lab/radiology report status based on test type."""
    test = args.get("test_type", "report").lower()
    patient = args.get("patient_name", "the patient")
    
    if any(k in test for k in ["blood", "urine", "sugar"]):
        return {"answer": f"The lab results for {patient}'s blood test are ready. You can collect the hard copy from the ground floor counter or view it on our mobile app."}
    
    if any(k in test for k in ["mri", "ct", "x-ray", "xray"]):
        return {"answer": f"The radiologist is currently reviewing the {test} for {patient}. It should be finalized by 6 PM this evening."}

    return {"answer": f"I've checked the records for {patient}. Some reports are still pending. Please check back in a few hours."}


def emergency_handoff(args: dict) -> dict:
    """Handoff for emergencies - logs and signals escalation."""
    logger.warning("!!! EMERGENCY HANDOFF TRIGGERED !!!")
    return {"answer": "Escalating call to emergency staff...", "status": "ESCALATED"}


# ---------------------------------------------------------------------------
# Tool specifications for Nova Sonic (Asha)
# ---------------------------------------------------------------------------

_hospital_info_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The specific hospital inquiry (e.g., address, pharmacy timings)",
        }
    },
    "required": ["query"],
})

_doctor_availability_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Department or doctor name",
        }
    },
    "required": ["query"],
})

_appointment_booking_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "doctor_dept": {"type": "string"},
        "date": {"type": "string"},
        "time": {"type": "string"},
        "patient_name": {"type": "string"},
        "symptom_intent": {"type": "string", "description": "Patient's primary intent, needs, or symptoms as per Requirement No 2."},
    },
    "required": ["doctor_dept", "date"],
})

_report_status_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_name": {"type": "string"},
        "test_type": {"type": "string"},
    },
    "required": ["patient_name"],
})

_handoff_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
    },
    "required": ["reason"],
})

available_tools: list[dict] = [
    {
        "toolSpec": {
            "name": "hospitalInfoTool",
            "description": "Get hospital information like address, contact details, pharmacy timings, and policies.",
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
]

# Handler map keyed by lowercase tool name
_tool_handlers: dict[str, Any] = {
    "hospitalinfotool": hospital_info,
    "doctoravailabilitytool": doctor_availability,
    "appointmentbookingtool": appointment_booking,
    "reportstatustool": report_status,
    "handofftool": emergency_handoff,
}


async def tool_processor(tool_name: str, tool_args: str) -> dict[str, Any]:
    """Parse tool_args JSON, dispatch to the handler, return result."""
    try:
        args = json.loads(tool_args)
    except (json.JSONDecodeError, TypeError):
        args = {}
    handler = _tool_handlers.get(tool_name.lower())
    if handler is None:
        return {"message": "I cannot help you with that request", "success": False}
    
    # Hospital tools are currently mocked as sync functions
    # (In real project these would be async API calls)
    if asyncio.iscoroutinefunction(handler):
        return await handler(args)
    
    # Run in executor to be safe even if mocked
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, handler, args)

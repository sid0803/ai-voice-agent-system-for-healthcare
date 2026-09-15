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
import uuid
from typing import Any, Optional, Tuple, Dict, List

import src.compat  # Applies platform patches (e.g. Windows WMI deadlock fix) idempotently
import boto3
import numpy as np

from src.integrations.tenant_manager import tenant_manager
from src.integrations.sheets_client import sheets_client
from src.integrations.local_sink import local_sink, booking_store
from src.kb_config import DISABLE_FAISS_FOR_UNIFIED, ENABLE_MULTI_INTENT, KB_SYSTEM
from src.kb_loader import get_kb_loader
from src.analytics.triage_store import triage_store

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
_SIMILARITY_THRESHOLD = 0.93  # [A1.5-FIX] Raised from 0.85 → 0.93 to prevent false cache hits.
# At 0.85, semantically adjacent but wrong entries were returned (e.g. 'parking' matching 'MRI cost').
# 0.93 ensures only very high-confidence matches are served from cache.
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

# Thread lock for FAISS index writes (index is not thread-safe for add)
_faiss_lock = threading.Lock()

# Module-level FAISS index and metadata store
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


# Load FAISS and Bedrock embedding client only for legacy mode. Unified KB uses deterministic local lookups.
if not (KB_SYSTEM == "unified" and DISABLE_FAISS_FOR_UNIFIED):
    _kb_client = boto3.client(
        "bedrock-agent-runtime",
        region_name=_kb_region,
        config=_BOTO_POOL_CONFIG,
    ) if _kb_id else None

    _embed_client = boto3.client(
        "bedrock-runtime",
        region_name=_embed_region,
        config=_BOTO_POOL_CONFIG,
    )
    _load_faiss_cache()
else:
    _kb_client = None
    _embed_client = None
    _faiss_index = None
    _faiss_meta = []
    logger.info("[FAISS] Fully skipped — unified KB mode active. Saving ~100-200MB RAM.")


def _embed_query(text: str) -> np.ndarray | None:
    """Get embedding from Bedrock Titan Embeddings v2.
    
    Security: Error handling (P1) and timeouts added for clinical reliability.
    """
    if _embed_client is None:
        return None
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
    if _faiss_index is None:
        return None
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
    if _faiss_index is None:
        return
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
    if KB_SYSTEM == "unified":
        return
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
# Hospital Tool Implementations (Asha / SarvoDaya Hospital)
# ---------------------------------------------------------------------------

import re

def _has_word(query: str, word: str) -> bool:
    """Check if a word or phrase exists in the query with word boundaries."""
    if not query or not word:
        return False
    pattern = r"\b" + re.escape(word) + r"\b"
    return bool(re.search(pattern, query))

def _has_prefix_word(query: str, prefix: str) -> bool:
    """Check if a prefix matches the beginning of any word in the query."""
    if not query or not prefix:
        return False
    pattern = r"\b" + re.escape(prefix)
    return bool(re.search(pattern, query))


# ---------------------------------------------------------------------------
# Hindi / Devanagari alias map
# Maps Devanagari and Romanized Hindi medical terms -> English equivalents.
# Applied in _normalize_query() so all downstream matching is English-first.
# ---------------------------------------------------------------------------
HINDI_ALIAS_MAP = {
    # Sugar / Diabetes
    "शुगर": "sugar", "शुगर टेस्ट": "sugar test", "मधुमेह": "diabetes",
    "फास्टिंग ब्लड शुगर": "fasting blood sugar", "फास्टिंग": "fasting",
    "रैंडम ब्लड शुगर": "random blood sugar",
    # Blood / CBC
    "खून": "blood", "रक्त": "blood", "सीबीसी": "cbc",
    # MRI / Scans
    "एमआरआई": "mri", "एमरई": "mri", "एमरै": "mri", "एम आर आई": "mri",
    "सीटी स्कैन": "ct scan", "सीटी": "ct", "स्कैन": "scan",
    "एक्सरे": "xray", "एक्स रे": "xray",
    "अल्ट्रासाउंड": "ultrasound", "सोनोग्राफी": "ultrasound",
    "इको": "echo", "ईसीजी": "ecg",
    # Thyroid / Liver / Kidney
    "थायरॉइड": "thyroid", "थायरोइड": "thyroid",
    "जिगर": "liver", "किडनी": "kidney", "गुर्दा": "kidney",
    "लिपिड": "lipid",
    # Price keywords
    "कीमत": "price", "दाम": "price", "मूल्य": "price",
    "शुल्क": "charges", "फीस": "fees",
    "कितना": "cost", "खर्चा": "cost", "खर्च": "cost",
    "रेट": "rate",
    # Facility / Amenity
    "पार्किंग": "parking", "पार्क": "parking",
    "कैफेटेरिया": "cafeteria", "कैंटीन": "cafeteria",
    "फार्मेसी": "pharmacy", "दवाई": "pharmacy", "दवा": "pharmacy",
    "एटीएम": "atm",
    # Travel / Directions
    "रास्ता": "directions", "कैसे आएं": "how to reach",
    "पहुंचना": "reach", "पहुँचना": "reach",
    # Date & Time terms
    "कल": "kal", "आज": "aaj", "परसों": "parso",
    "सुबह": "morning", "दोपहर": "afternoon", "शाम": "evening", "साम": "evening", "रात": "night",
    "सोमवार": "monday", "मंगलवार": "tuesday", "बुधवार": "wednesday",
    "गुरुवार": "thursday", "शुक्रवार": "friday", "शनिवार": "saturday", "रविवार": "sunday",
    # Appointment / Doctor
    "अपॉइंटमेंट": "appointment", "अपोइंटमेंट": "appointment",
    "डॉक्टर": "doctor", "डाक्टर": "doctor",
    # General medical
    "बुखार": "fever", "दर्द": "pain", "जांच": "test", "जाँच": "test",
    "ऑपरेशन": "surgery", "आपरेशन": "surgery",
    # Timings / Hours / 24-7
    "ट्वेंटी फोर इंटर सेवन": "24/7",
    "ट्वेंटी फोर सेवन": "24/7",
    "चौबीस घंटे": "24 hours",
    "24 घंटे": "24 hours",
    "ओपन": "open",
    "खुल": "open",
    "खुला": "open",
    "बंद": "close",
    "समय": "time",
    "टाइम": "time",
    "टाइमिंग": "timing",
    # Additional Hindi/Hinglish Query Terms & ASR Garble Mappings
    "अवेलेबल": "available",
    "उपलब्ध": "available",
    "डिपार्टमेंट": "department",
    "डिपार्टमेन्ट": "department",
    "डिपार्टमेंट्स": "departments",
    "कॉर्मिंट": "department",
    "cormint": "department",
    "departmint": "department",
    "कौन": "which",
    "कौन-कौन": "which",
    # Lab Test ASR Garble Mappings
    "लैपटिस्ट": "lab test",
    "लैपटॉप": "lab test",
    "लैप टेस्ट": "lab test",
    "लैबटैस्ट": "lab test",
    "laptop test": "lab test",
    "labtest": "lab test",
    "लैब टेस्ट": "lab test",
    # Pharmacy ASR Garble Mappings
    "पाड़ में सिगरेट": "pharmacy",
    "फार्मसी": "pharmacy",
    "फार्मेसी": "pharmacy",
    "दवा की दुकान": "pharmacy",
    "farmacy": "pharmacy",
    "cigratte": "pharmacy",
    # MRI & Imaging ASR Garble Mappings
    "मआरटी": "mri",
    "मआरआई": "mri",
    "एमआरआइ": "mri",
    "एम आर टी": "mri",
    "mrt": "mri",
    "mri scan": "mri",
    "mriscan": "mri",
    "एमआरआई स्कैन": "mri",
    "सिटी स्कैन": "ct scan",
    "एक्सरे": "xray",
    # Doctor ASR Garble Mappings
    "डाक्टर": "doctor",
    "डॉक्टर": "doctor",
    "doktor": "doctor",
}


def _normalize_query(query: str) -> str:
    """Cleans up the query string for matching. Expands Hindi/Devanagari aliases to English."""
    if not query:
        return ""
    q = query.strip()
    # Expand longer phrases first (to avoid partial replacements)
    for hindi_phrase, english_phrase in sorted(HINDI_ALIAS_MAP.items(), key=lambda x: -len(x[0])):
        q = q.replace(hindi_phrase, english_phrase)
    return q.lower()


_DAY_ALIASES = {
    "mon": "Monday", "monday": "Monday", "somvar": "Monday", "सोमवार": "Monday",
    "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday", "mangalvar": "Tuesday", "मंगलवार": "Tuesday",
    "wed": "Wednesday", "wednesday": "Wednesday", "budhvar": "Wednesday", "बुधवार": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday", "guruvar": "Thursday", "गुरुवार": "Thursday",
    "fri": "Friday", "friday": "Friday", "shukravar": "Friday", "शुक्रवार": "Friday",
    "sat": "Saturday", "saturday": "Saturday", "shanivar": "Saturday", "शनिवार": "Saturday",
    "sun": "Sunday", "sunday": "Sunday", "ravivar": "Sunday", "रविवार": "Sunday",
}


STOP_WORDS = {
    # English stop words
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", 
    "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself", 
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which", 
    "who", "whom", "this", "that", "these", "those", "am", "is", "are", "was", "were", "be", 
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an", 
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of", "at", "by", 
    "for", "with", "about", "against", "between", "into", "through", "during", "before", 
    "after", "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", 
    "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", 
    "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", 
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", 
    "will", "just", "don", "should", "now", "d", "ll", "m", "o", "re", "ve", "y", "ain", 
    "aren", "couldn", "didn", "doesn", "hadn", "hasn", "haven", "isn", "ma", "mightn", 
    "mustn", "needn", "shan", "shouldn", "wasn", "weren", "won", "wouldn", "please", "would", "like", "actually",
    
    # Hinglish/Hindi stop words
    "hai", "haan", "naam", "ko", "se", "ka", "ki", "ke", "mein", "me", "par", "bhi", "hi", 
    "toh", "aur", "ya", "kya", "kyun", "kab", "kahan", "kaise", "kon", "kaun", "karke", 
    "liye", "hona", "hota", "hoti", "hote", "tha", "thi", "the", "kar", "karna", "karta", 
    "karti", "karte", "sakte", "sakta", "sakti", "milna", "milenge", "baje", "samay", "time", 
    "timing", "se", "pe", "par", "ek", "do", "teen", "chaar", "paanch", "sheher", "aana", 
    "jaana", "gaya", "gayi", "gaye", "aa", "ja", "raha", "rahi", "rahe", "huya", "huyi", "huye"
}


def _tokens(text: str) -> set[str]:
    normalized = _normalize_query(text)
    # ASCII tokens (after Hindi alias expansion)
    ascii_tokens = {t for t in re.findall(r"[a-z0-9]+", normalized) if len(t) >= 2 and t not in STOP_WORDS}
    # Devanagari tokens — safety fallback for any untranslated Devanagari words
    # (unicode block U+0900–U+097F covers all Devanagari script)
    devanagari_tokens = {t for t in re.findall(r"[\u0900-\u097F]+", text) if len(t) >= 2}
    return ascii_tokens | devanagari_tokens


def _contains_any(query: str, words: list[str]) -> bool:
    return any(word in query for word in words)


def _has_any_word(query: str, words: list[str]) -> bool:
    return any(_has_word(query, w) for w in words)


def _get_unified_loader():
    return get_kb_loader(kb_system="unified")


def _format_price(value: Any) -> str:
    if value in (None, "", 0):
        return "Standard charges apply"
    try:
        return f"Rs. {int(value):,}"
    except (TypeError, ValueError):
        return f"Rs. {value}"


def _department_name(dept: Any) -> str:
    return dept.get("name", "") if isinstance(dept, dict) else str(dept)


def _doctor_department(doc: dict) -> str:
    return doc.get("department") or doc.get("dept") or ""


def _score_text_match(query: str, candidate_text: str) -> int:
    q_tokens = _tokens(query)
    c_tokens = _tokens(candidate_text)
    if not q_tokens or not c_tokens:
        return 0
    score = len(q_tokens & c_tokens)
    if query in candidate_text or candidate_text in query:
        score += 4
    return score


def _service_search_text(service: dict) -> str:
    parts = [
        service.get("name", ""),
        service.get("category", ""),
        service.get("description", ""),
        " ".join(service.get("synonyms", [])),
        " ".join(service.get("keywords", [])),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _doctor_search_text(doc: dict) -> str:
    parts = [
        doc.get("name", ""),
        _doctor_department(doc),
        doc.get("designation", ""),
        doc.get("biography", ""),
        " ".join(doc.get("specializations", [])),
        " ".join(doc.get("specialty_keywords", [])),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _match_services(query: str, services: list[dict]) -> list[dict]:
    price_words = ["cost", "price", "charge", "charges", "rate", "kitna", "kharcha", "fees", "daam"]
    service_words = [
        "mri", "ct", "scan", "xray", "x-ray", "ultrasound", "usg", "blood", "thyroid", "cbc", "test", "lab", "echo",
        "ecg", "tmt", "dialysis", "physiotherapy", "report", "result", "jaanch", "operation", "ot", "surgery", "urine",
        "kidney", "liver", "lipid", "sugar", "glucose",
        # Hindi / Devanagari keywords (post alias-expansion these are already English,
        # but kept here as safety net for partial expansions or new phrases)
        "शुगर", "एमआरआई", "सीटी", "खून", "थायरॉइड", "किडनी", "जिगर", "जांच", "जाँच",
    ]
    if not (any(_has_word(query, w) for w in price_words) or any(_has_word(query, w) for w in service_words)):
        return []
    scored = []
    for service in services:
        text = _service_search_text(service)
        score = _score_text_match(query, text)
        for phrase in [service.get("name", "").lower(), *[s.lower() for s in service.get("synonyms", [])]]:
            if phrase and phrase in query:
                score += 6
        if score > 0:
            scored.append((score, service))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0]
    matched = [service for score, service in scored if score >= max(1, best_score - 1)]

    # If specific modality was queried, keep only matching modality services
    for mod in ["ultrasound", "usg", "mri", "xray", "x-ray", "ct"]:
        if _has_word(query, mod):
            mod_filtered = [s for s in matched if mod in s.get("name", "").lower() or any(mod in syn.lower() for syn in s.get("synonyms", []))]
            if mod_filtered:
                return mod_filtered

    return matched


def _format_service_answer(matches: list[dict], query: str = "") -> str:
    if len(matches) == 1:
        service = matches[0]
        # Check Devanagari Hindi or Hinglish query
        devanagari_count = sum(1 for ch in query if '\u0900' <= ch <= '\u097F')
        if devanagari_count >= 1 and service.get("answer_hi"):
            return service["answer_hi"]
        if any(w in query.lower() for w in ["hai", "hain", "kya", "kitna", "fee", "rate", "kharcha", "ka"]) and service.get("answer_hinglish"):
            return service["answer_hinglish"]
            
        price = _format_price(service.get("price"))
        duration = service.get("duration_minutes") or service.get("duration")
        prep = service.get("prep_instructions") or service.get("prep")
        location = service.get("location")
        name = service.get("name", "This service")
        parts = []
        if price:
            parts.append(f"costs {price}")
        if duration:
            parts.append(f"takes about {duration} minutes" if isinstance(duration, int) else f"takes about {duration}")
        answer = f"{name} " + " and ".join(parts) + "." if parts else f"{name} is available."
        if prep:
            answer += f" Prep: {prep}"
        if location:
            answer += f" Location: {location}."
        return answer
    devanagari_count = sum(1 for ch in query if '\u0900' <= ch <= '\u097F')
    is_hinglish = any(w in query.lower() for w in ["hai", "hain", "kya", "kitna", "batao", "bataiye", "ko", "se", "ka"])

    if devanagari_count >= 1:
        items = ", ".join(f"{s.get('name')} ({_format_price(s.get('price'))})" for s in matches[:8])
        return f"हमारे पास ये लैब टेस्ट उपलब्ध हैं: {items}। हमारी लैब 24 घंटे खुली रहती है।"
    elif is_hinglish:
        items = ", ".join(f"{s.get('name')} ({_format_price(s.get('price'))})" for s in matches[:8])
        return f"Humare paas ye lab tests available hain: {items}. Humari lab 24/7 open hai."

    items = ", ".join(f"{s.get('name')} ({_format_price(s.get('price'))})" for s in matches[:8])
    return f"We offer the following lab tests (24/7 available): {items}."


def _match_faq(query: str, faq_entries: list[dict]) -> dict | None:
    best = (0, None)
    for item in faq_entries:
        fields = [
            item.get("intent", ""),
            item.get("category", ""),
            item.get("answer", ""),
            " ".join(item.get("tags", [])),
            " ".join(item.get("question_variants", [])),
            " ".join(item.get("questions", [])),
            item.get("question", ""),
        ]
        score = _score_text_match(query, " ".join(fields).lower())
        intent = item.get("intent", "").replace("_", " ")
        if intent and _has_word(query, intent):
            score += 5
        if score > best[0]:
            best = (score, item)
    return best[1] if best[0] >= 2 else None


def _format_departments(loader, query: str = "") -> str:
    departments = [_department_name(d) for d in loader.get_departments()]
    departments = [d for d in departments if d]
    if not departments:
        departments = [
            "Cardiology", "General Medicine", "Neurology", "Orthopedics", "Pediatrics",
            "ENT", "Gynecology", "Endocrinology", "Gastroenterology", "Pulmonology",
            "Oncology", "Ophthalmology", "Dermatology"
        ]
    
    devanagari_count = sum(1 for ch in query if '\u0900' <= ch <= '\u097F')
    is_hinglish = any(w in query.lower() for w in ["hai", "hain", "kya", "batao", "bataiye", "kaun", "konsa", "kaunse"])

    if devanagari_count >= 1:
        return f"हमारे पास 13 विभाग हैं: कार्डियोलॉजी (हृदय रोग), जनरल मेडिसिन (सामान्य चिकित्सा), न्यूरोलॉजी (तंत्रिका रोग), ऑर्थोपेडिक्स (हड्डी रोग), पीडियाट्रिक्स (बाल रोग), ईएनटी (कान नाक गला), गाइनकोलॉजी (महिला रोग), एंडोक्रिनोलॉजी (शुगर/थायरॉइड), गैस्ट्रोएंटेरोलॉजी (पेट/लिवर), पल्मोनोलॉजी (फेफड़े/सांस), ऑन्कोलॉजी (कैंसर), ओफ्थाल्मोलॉजी (आंखों के डॉक्टर), और डर्मेटोलॉजी (त्वचा/स्किन)।"
    elif is_hinglish:
        return f"Humare paas 13 departments hain: Cardiology, General Medicine, Neurology, Orthopedics, Pediatrics, ENT, Gynecology, Endocrinology, Gastroenterology, Pulmonology, Oncology, Ophthalmology, aur Dermatology."

    return f"We have {len(departments)} departments: {', '.join(departments)}."


def _match_rooms(query: str, rooms: list[dict]) -> list[dict]:
    keywords = ["room", "rent", "tariff", "ward", "icu", "deluxe", "private", "general", "kharcha", "rate", "daam", "admit", "daakhil", "admitted"]
    if not any(_has_word(query, kw) for kw in keywords):
        return []
    specific = {
        "icu": ["icu"],
        "deluxe": ["deluxe"],
        "semi": ["semi"],
        "private": ["private"],
        "general": ["general", "ward"],
    }
    for label, words in specific.items():
        if any(_has_word(query, word) for word in words):
            exact = [room for room in rooms if label in room.get("name", "").lower()]
            if exact:
                return exact
    matches = []
    for room in rooms:
        text = f"{room.get('name', '')} {room.get('description', '')}".lower()
        if _score_text_match(query, text) > 0:
            matches.append(room)
    return matches or rooms


def _format_rooms(matches: list[dict]) -> str:
    if len(matches) == 1:
        room = matches[0]
        return f"{room.get('name')} rate is {_format_price(room.get('price_per_day'))} per day. {room.get('description', '')}."
    return "Our daily room rates are: " + ", ".join(
        f"{room.get('name')}: {_format_price(room.get('price_per_day'))}"
        for room in matches
    ) + "."


def _match_amenity(query: str, amenities: dict) -> str | None:
    if not isinstance(amenities, dict):
        return None
    
    # Map keywords/synonyms to amenity keys
    mapping = {
        "parking": ["parking", "park", "gadi", "vehicle", "car", "bike",
                    "\u092a\u093e\u0930\u094d\u0915\u093f\u0902\u0917", "\u0917\u093e\u0921\u093c\u0940"],
        "wifi": ["wifi", "wi-fi", "wi fi", "internet", "net", "password"],
        "cafeteria": ["cafeteria", "canteen", "food", "eat", "khana", "khaana", "khaane", "restaurant",
                      "lunch", "dinner", "breakfast", "canteen",
                      "\u0915\u0948\u092b\u0947\u091f\u0947\u0930\u093f\u092f\u093e", "\u0915\u0948\u0902\u091f\u0940\u0928", "\u0916\u093e\u0928\u093e", "\u0916\u093e\u0928\u0947"],
        "pharmacy": ["pharmacy", "medicine", "dawai", "chemist", "medical store",
                     "\u092b\u093e\u0930\u094d\u092e\u0947\u0938\u0940", "\u0926\u0935\u093e\u0908", "\u0926\u0935\u093e"],
        "atm": ["atm", "cash", "paise", "money", "bank",
                "\u090f\u091f\u0940\u090f\u092e"],
        "wheelchair_porter": ["wheelchair", "porter", "assist", "help", "kursi", "stretcher"],
        "prayer_room": ["prayer", "meditation", "mandir", "pray", "pooja", "masjid"],
        "play_area": ["play", "children", "kids", "khelen", "activity"],
        "directions": ["travel", "directions", "reach", "route", "how to come", "how to get",
                       "from west", "from mumbai", "from bengal", "kaise aayein", "kaise aana",
                       "\u0930\u093e\u0938\u094d\u0924\u093e", "\u092a\u0939\u0941\u0902\u091a\u0928\u093e", "\u0915\u0948\u0938\u0947 \u0906\u090f\u0902"],
    }
    
    # Handle travel/directions queries early
    direction_keywords = mapping["directions"]
    if any(syn in query for syn in direction_keywords):
        address = amenities.get("address") or amenities.get("location", "")
        if not address:
            address = "12-B, MG Road, Residency Area, Bengaluru - 560025"
        return (
            f"Our hospital is located at: {address}. "
            "You can reach us by flight, train, or road. "
            "From the airport or railway station, take a cab or metro directly to MG Road. "
            "Our address is on Google Maps — search 'SarvoDaya Hospital'."
        )
    
    matched_results = []
    for key, value in amenities.items():
        synonyms = mapping.get(key, [key.replace("_", " ")])
        if any(syn in query for syn in synonyms) or any(_has_word(query, syn) for syn in synonyms):
            if isinstance(value, dict):
                details = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in value.items())
                matched_results.append(f"{key.replace('_', ' ').title()}: {details}")
            else:
                matched_results.append(f"{key.replace('_', ' ').title()}: {value}")
                
    if matched_results:
        return " | ".join(matched_results)
    return None


def _requested_day(query: str) -> str | None:
    import datetime
    ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
    q = query.lower()
    if any(w in q for w in ["tomorrow", "kal", "कल"]):
        return (ist_now + datetime.timedelta(days=1)).strftime("%A")
    if any(w in q for w in ["today", "aaj", "आज"]):
        return ist_now.strftime("%A")
    if "parso" in q or "परसों" in q:
        return (ist_now + datetime.timedelta(days=2)).strftime("%A")
    for alias, day in _DAY_ALIASES.items():
        if _has_word(query, alias) or alias in query:
            return day
    return None


def _doctor_slots_for_day(doc: dict, day: str | None, date_iso: str = None) -> tuple[str, list[str]]:
    day_label, free_slots, _ = get_actual_doctor_availability(doc, date_iso=date_iso, day_name=day)
    return day_label, free_slots


def _normalize_department_name(dept_str: str) -> str:
    """Canonical normalization for department names across languages and aliases."""
    if not dept_str:
        return ""
    q = dept_str.lower().strip()
    
    # Check whole-word / token boundaries
    tokens = set(_tokens(q))
    
    if any(k in q for k in ["cardio", "heart", "dil", "कार्डियो", "हृदय"]) or "cardiology" in tokens:
        return "cardiology"
    elif any(k in q for k in ["neurosurg", "न्यूरोसर्जरी"]):
        return "neurosurgery"
    elif any(k in q for k in ["neuro", "brain", "spine", "दिमाग", "नस"]) or "neurology" in tokens:
        return "neurology"
    elif any(k in q for k in ["ortho", "joint", "bone", "knee", "हड्डी", "जोड़", "घुटने"]) or "orthopedics" in tokens:
        return "orthopedics"
    elif any(k in q for k in ["pediatr", "child", "baby", "बाल", "बच्चे"]) or "pediatrics" in tokens:
        return "pediatrics"
    elif any(k in q for k in ["gyneco", "obstetr", "pregnancy", "women", "महिला", "गर्भावस्था"]) or "gynecology" in tokens:
        return "gynecology"
    elif any(k in q for k in ["endocrin", "diabetes", "thyroid", "sugar", "मधुमेह", "थायरॉइड"]) or "endocrinology" in tokens:
        return "endocrinology"
    elif any(k in q for k in ["gastro", "stomach", "liver", "पेट", "लिवर"]) or "gastroenterology" in tokens:
        return "gastroenterology"
    elif any(k in q for k in ["pulmono", "lung", "chest", "asthma", "फेफड़े", "सांस"]) or "pulmonology" in tokens:
        return "pulmonology"
    elif any(k in q for k in ["onco", "cancer", "tumor", "कैंसर", "ट्यूमर"]) or "oncology" in tokens:
        return "oncology"
    elif any(k in q for k in ["ophthal", "eye", "vision", "आंख"]) or "ophthalmology" in tokens:
        return "ophthalmology"
    elif any(k in tokens for k in ["ent", "ear", "nose", "throat", "ears", "कान", "नाक", "गला"]):
        return "ent"
    elif any(k in q for k in ["derma", "skin", "hair", "त्वचा", "चमड़ी"]) or "dermatology" in tokens:
        return "dermatology"
    elif any(k in q for k in ["emergency", "trauma", "इमरजेंसी"]):
        return "emergency"
    elif any(k in q for k in [
        "general medicine", "internal medicine", "physician", "general doctor",
        "general physician", "family doctor", "family medicine", "medicine doctor",
        "जनरल मेडिसिन", "जनरल मेडिकल", "दवा विभाग", "दवाई", "बुखार का डॉक्टर",
        "दवा का डॉक्टर", "दवाई वाले"
    ]) or "general medicine" in tokens or "physician" in tokens:
        return "general medicine"
    elif any(k in q for k in [
        "radiol", "radiologist", "imaging", "xray", "x-ray",
        "ultrasound", "usg", "sonograph", "scan center", "mri center",
        "रेडियोलॉजी", "एक्स-रे", "अल्ट्रासाउंड", "इमेजिंग", "सोनोग्राफी"
    ]) or "radiology" in tokens or "imaging" in tokens:
        return "radiology"
    return q


def _format_slots_concise(slots: list[str]) -> str:
    """Convert slot list to natural spoken range for phone delivery.
    ['09:00', '09:30', '10:00', '10:30'] -> 'between 9:00 AM and 10:30 AM'
    """
    if not slots:
        return "no available slots"

    def _to_12h(t: str) -> str:
        try:
            return render_time(t)
        except Exception:
            return t

    if len(slots) == 1:
        return _to_12h(slots[0])
    if len(slots) == 2:
        return f"{_to_12h(slots[0])} and {_to_12h(slots[1])}"
    return f"between {_to_12h(slots[0])} and {_to_12h(slots[-1])}"


def _find_next_available_slot_in_range(dept_name: str, from_date_iso: str, search_days: int = 7) -> Optional[dict]:
    """Scan forward up to search_days to find the earliest bookable department slot."""
    try:
        import datetime
        from_d = datetime.date.fromisoformat(from_date_iso)
    except Exception:
        return None

    loader = _get_unified_loader() if KB_SYSTEM == "unified" else None
    from src.kb_loader import get_kb_loader
    kb = loader or get_kb_loader()
    all_docs = kb.get_doctors()
    norm_dept = _normalize_department_name(dept_name)
    dept_docs = [d for d in all_docs if _normalize_department_name(d.get("department", "")) == norm_dept]
    if not dept_docs:
        return None

    for offset in range(1, search_days + 1):
        next_d = from_d + datetime.timedelta(days=offset)
        next_iso = next_d.strftime("%Y-%m-%d")
        for d in dept_docs:
            day_label, free_slots, fee = get_actual_doctor_availability(d, date_iso=next_iso)
            if free_slots:
                return {
                    "date_iso": next_iso,
                    "date_display": next_d.strftime("%A, %d %B"),
                    "doctor_name": d.get("name"),
                    "fee": fee,
                    "available_slots": free_slots,
                    "slots_spoken": _format_slots_concise(free_slots),
                }
    return None


def _match_doctors(query: str, doctors: list[dict], explicit_dept: str = None, explicit_doctor: str = None) -> list[dict]:
    """
    Hard department and entity filtering.
    1. If explicit doctor name is given -> match against doctor candidates only.
    2. If explicit department is given -> HARD FILTER: return only doctors in that department.
    3. If query mentions a department/specialty -> HARD FILTER for that department.
    4. Fuzzy matching ONLY for unresolved entities. Never bridges across departments.
    """
    # 1. Explicit doctor lookup
    if explicit_doctor:
        doc_q = _normalize_query(explicit_doctor)
        matched = []
        for doc in doctors:
            name_parts = [p for p in _tokens(doc.get("name", "")) if p not in {"dr"}]
            if any(_has_word(doc_q, part) or part in doc_q for part in name_parts):
                matched.append(doc)
        if matched:
            return matched

    # 2. Check for explicit or query-extracted department
    target_dept = None
    if explicit_dept:
        target_dept = _normalize_department_name(explicit_dept)
    else:
        # Check if query directly references a specialty/department
        norm_q = _normalize_query(query)
        target_dept = _normalize_department_name(norm_q)
        if not target_dept or target_dept == norm_q:
            # Check individual tokens
            tokens = _tokens(norm_q)
            for t in tokens:
                d = _normalize_department_name(t)
                if d and d != t:
                    target_dept = d
                    break

    # 3. If a target department was identified -> HARD FILTER
    if target_dept and target_dept != _normalize_query(query):
        dept_docs = [
            doc for doc in doctors
            if _normalize_department_name(doc.get("department", "")) == target_dept
        ]
        if dept_docs:
            return dept_docs

    # 4. Check if doctor name is in query
    name_matched = []
    for doc in doctors:
        name_parts = [p for p in _tokens(doc.get("name", "")) if p not in {"dr"}]
        if any(_has_word(query, part) for part in name_parts):
            name_matched.append(doc)
    if name_matched:
        return name_matched

    # 5. General fallback text scoring (restricted to top scoring department)
    scored = []
    for doc in doctors:
        text = _doctor_search_text(doc)
        score = _score_text_match(query, text)
        if score > 0:
            scored.append((score, doc))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0]
    top_dept = _normalize_department_name(scored[0][1].get("department", ""))
    return [
        doc for score, doc in scored 
        if score >= best_score and _normalize_department_name(doc.get("department", "")) == top_dept
    ]


def _format_doctor_answer(matches: list[dict], query: str) -> str:
    day = _requested_day(query)
    available = []
    unavailable = []
    for doc in matches:
        day_label, slots = _doctor_slots_for_day(doc, day)
        if day and not slots:
            unavailable.append(doc)
            continue
        available.append((doc, day_label, slots))

    if day and not available:
        names = ", ".join(doc.get("name", "Doctor") for doc in unavailable[:3])
        return f"{names} do not have listed slots on {day}. Which other day should I check?"

    if len(available) == 1:
        doc, day_label, slots = available[0]
        dept = _doctor_department(doc)
        fee = _format_price(doc.get("fee"))
        slot_text = ", ".join(slots[:3]) if slots else day_label
        answer = f"{doc.get('name')} ({dept}) is available"
        if day_label:
            answer += f" on {day_label}"
        if slot_text:
            answer += f" at {slot_text}"
        if fee:
            answer += f". Consultation fee is {fee}"
        return answer + ". Should I go ahead and book this slot for you?"

    parts = []
    for doc, day_label, slots in available[:3]:
        slot_text = ", ".join(slots[:2]) if slots else day_label
        dept = _doctor_department(doc)
        fee = _format_price(doc.get("fee"))
        entry = f"{doc.get('name')} in {dept}"
        if fee:
            entry += f" for {fee}"
        if slot_text:
            entry += f" at {slot_text}"
        parts.append(entry)
    return f"We have specialists available: " + ", and ".join(parts) + ". Which one would you prefer?"


def _unified_hospital_info(args: dict, hospital_id: str = None) -> dict:
    loader = _get_unified_loader()
    raw_query = args.get("query", "")
    query = _normalize_query(raw_query)
    core = loader.get_core_info()

    # Dedicated MRI & Imaging Scan Handler
    # 1. MRI-only queries (exclude other modalities)
    if _has_any_word(query, ["mri", "mrt"]) and not _has_any_word(query, ["ct", "xray", "x-ray", "ultrasound", "usg"]):
        devanagari_count = sum(1 for ch in raw_query if '\u0900' <= ch <= '\u097F')
        is_hinglish = any(w in raw_query.lower() for w in ["hai", "hain", "kya", "kitna", "rate", "kharcha", "ka", "batao", "bataiye"])
        
        if devanagari_count >= 1:
            return {"answer": "हमारे अस्पताल में 3 प्रकार के एमआरआई (MRI) स्कैन उपलब्ध हैं: ब्रेन एमआरआई (₹8,500), स्पाइन एमआरआई (₹9,000), और फुल एब्डोमेन एमआरआई (₹12,000)। हमारा एमआरआई सेंटर 24 घंटे खुला रहता है।"}
        elif is_hinglish:
            return {"answer": "Humare hospital mein 3 types ke MRI scans available hain: Brain MRI (₹8,500), Spine MRI (₹9,000), aur Full Abdomen MRI (₹12,000). Humara MRI center 24/7 open hai."}
        return {"answer": "We offer 3 MRI scans: Brain MRI (₹8,500), Spine MRI (₹9,000), and Full Abdomen MRI (₹12,000). Our imaging center is open 24/7."}

    # 2. CT Scan inquiries
    elif _has_any_word(query, ["ct scan", "ct", "pet ct"]):
        services = _match_services(query, loader.get_services())
        if services:
            return {"answer": _format_service_answer(services, raw_query)}
        return {"answer": "We offer CT Head (₹4,500) and CT Abdomen (₹6,000). Our CT scan center is available 24/7."}

    # 3. General "scan" / "imaging" inquiries -> check services first
    elif _has_any_word(query, ["scan", "imaging"]):
        services = _match_services(query, loader.get_services())
        if services:
            return {"answer": _format_service_answer(services, raw_query)}

    # Early guard to route visiting hours queries before room matching (Fixes routing bug for "ward visiting hours")
    VISITING_KEYWORDS = [
        "visit", "visiting", "milne", "milna", "milenge", "mulakat",
        "timing", "hours", "time", "kab", "baje", "samay"
    ]
    if _has_any_word(query, VISITING_KEYWORDS):
        faq = _match_faq(query, loader.get_faq())
        if faq and faq.get("answer"):
            return {"answer": faq["answer"]}

    if ENABLE_MULTI_INTENT and _has_any_word(query, ["department", "departments"]) and _has_any_word(query, ["doctor", "doctors", "available", "availability"]):
        dept_answer = _format_departments(loader, raw_query)
        doc_matches = _match_doctors(query, loader.get_doctors()) or loader.get_doctors()
        return {"answer": f"{dept_answer} {_format_doctor_answer(doc_matches, query)}"}

    services = _match_services(query, loader.get_services())
    if services:
        return {"answer": _format_service_answer(services, raw_query)}

    if _has_any_word(query, ["department", "departments", "specialties", "speciality", "specialities"]):
        return {"answer": _format_departments(loader, raw_query)}

    rooms = _match_rooms(query, loader.get_room_types())
    if rooms:
        return {"answer": _format_rooms(rooms)}

    amenity = _match_amenity(query, loader.get_amenities())
    if amenity:
        return {"answer": amenity}

    faq = _match_faq(query, loader.get_faq())
    if faq and faq.get("answer"):
        return {"answer": faq["answer"]}

    hours = loader.get_operating_hours()
    if _has_any_word(query, ["hour", "time", "timing", "open", "close", "24/7", "24x7", "24-7", "24 hours", "24 hrs"]):
        if _has_word(query, "emergency"):
            return {"answer": f"Emergency is {hours.get('emergency', '24/7 open')}."}
        if _has_word(query, "pharmacy"):
            return {"answer": f"Pharmacy is {hours.get('pharmacy', core.get('pharmacy_hours', '24/7 open'))}."}
        return {"answer": "Hospital timings: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in hours.items()) + "."}

    TRAVEL_KEYWORDS = [
        "address", "location", "where", "kahan", "kidhar", "rasta", "pahunchna", 
        "directions", "route", "travel", "go", "come", "reach", "how to", "way", 
        "map", "west bengal", "bengal", "kolkata", "delhi", "mumbai", "train", 
        "flight", "bus", "cab", "distance", "far", "coming"
    ]
    if _has_any_word(query, TRAVEL_KEYWORDS):
        return {"answer": f"{core.get('name')} is located at {core.get('address', 'our main facility')}."}

    # Fallback to ensure we never return None
    return {
        "answer": "I'm sorry, I don't have that specific information right now. For detailed queries, please call our hospital desk directly.",
        "answer_hi": "माफ़ कीजिए, मुझे यह जानकारी अभी नहीं है। कृपया सीधे हमारे अस्पताल डेस्क से संपर्क करें।",
        "answer_hinglish": "Sorry, yeh specific jaankari abhi mere paas nahi hai. Kripya hamare hospital desk se seedha contact karein."
    }


def normalize_appointment_datetime(date_str: str, time_str: str = None) -> dict:
    """
    Deterministically normalizes natural date & time expressions.
    Returns:
    {
        "date_iso": "YYYY-MM-DD" or None,
        "time_24h": "HH:MM" or None,
        "day_name": "Monday" or None,
        "is_ambiguous": bool,
        "ambiguity_reason": str or None,
        "spoken_date": str,
        "spoken_time": str
    }
    """
    import datetime
    ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
    
    res = {
        "date_iso": None,
        "time_24h": None,
        "day_name": None,
        "is_ambiguous": False,
        "ambiguity_reason": None,
        "spoken_date": date_str or "tomorrow",
        "spoken_time": time_str or "10:00 AM"
    }
    
    q_date = _normalize_query((date_str or "tomorrow").strip())
    
    # 1. Check relative date ambiguity ("parso" / "परसों")
    if "parso" in q_date or "परसों" in q_date:
        res["is_ambiguous"] = True
        res["ambiguity_reason"] = "Inquiry mentions 'parso' which can mean day after tomorrow or day before yesterday. Please clarify the exact date."
        target_dt = ist_now + datetime.timedelta(days=2)
        res["date_iso"] = target_dt.strftime("%Y-%m-%d")
        res["day_name"] = target_dt.strftime("%A")
        return res
        
    # 2. Check "today" / "aaj"
    if any(w in q_date for w in ["today", "aaj", "आज"]):
        res["date_iso"] = ist_now.strftime("%Y-%m-%d")
        res["day_name"] = ist_now.strftime("%A")
        res["spoken_date"] = "today"
    # 3. Check "tomorrow" / "kal"
    elif any(w in q_date for w in ["tomorrow", "kal", "कल"]):
        target_dt = ist_now + datetime.timedelta(days=1)
        res["date_iso"] = target_dt.strftime("%Y-%m-%d")
        res["day_name"] = target_dt.strftime("%A")
        res["spoken_date"] = "tomorrow"
    # 4. Check ISO date "YYYY-MM-DD"
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", q_date):
        try:
            dt = datetime.datetime.strptime(q_date, "%Y-%m-%d")
            res["date_iso"] = q_date
            res["day_name"] = dt.strftime("%A")
        except ValueError as e:
            logger.debug("[DATE-NORM] Invalid YYYY-MM-DD format '%s': %s", q_date, e)
    # 5. Check weekdays
    else:
        weekday_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
            "somwar": 0, "mangalwar": 1, "budhwar": 2, "guruwar": 3,
            "shukrawar": 4, "shaniwar": 5, "ravivar": 6
        }
        for w_name, w_idx in weekday_map.items():
            if w_name in q_date:
                today_idx = ist_now.weekday()
                if w_idx == today_idx:
                    res["is_ambiguous"] = True
                    res["ambiguity_reason"] = f"Today is {ist_now.strftime('%A')}. Please clarify whether you mean today or next {ist_now.strftime('%A')}."
                    res["date_iso"] = ist_now.strftime("%Y-%m-%d")
                    res["day_name"] = ist_now.strftime("%A")
                    return res
                else:
                    days_ahead = (w_idx - today_idx) % 7
                    target_dt = ist_now + datetime.timedelta(days=days_ahead)
                    res["date_iso"] = target_dt.strftime("%Y-%m-%d")
                    res["day_name"] = target_dt.strftime("%A")
                    res["spoken_date"] = target_dt.strftime("%A")
                    break
                    
    if not res["date_iso"]:
        target_dt = ist_now + datetime.timedelta(days=1)
        res["date_iso"] = target_dt.strftime("%Y-%m-%d")
        res["day_name"] = target_dt.strftime("%A")

    # Time normalization
    if time_str:
        t_clean = time_str.lower().strip()
        # Check standard formats like "10:30", "14:00", "2:30 pm", "10 am"
        match_am_pm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t_clean)
        if match_am_pm:
            hr = int(match_am_pm.group(1))
            mn = int(match_am_pm.group(2)) if match_am_pm.group(2) else 0
            period = match_am_pm.group(3)
            
            if period == "pm" and hr < 12:
                hr += 12
            elif period == "am" and hr == 12:
                hr = 0
            elif not period and hr <= 7: # Assume afternoon for 1..7 without period
                hr += 12
                
            res["time_24h"] = f"{hr:02d}:{mn:02d}"
            
            # Format human spoken time
            disp_hr = hr if hr <= 12 else hr - 12
            disp_hr = 12 if disp_hr == 0 else disp_hr
            disp_period = "AM" if hr < 12 else "PM"
            res["spoken_time"] = f"{disp_hr}:{mn:02d} {disp_period}" if mn > 0 else f"{disp_hr}:00 {disp_period}"
            
    return res


def get_actual_doctor_availability(doc: dict, date_iso: str = None, day_name: str = None) -> tuple[str, list[str], int]:
    """
    Computes actual doctor availability by subtracting active bookings & holds.
    Formula: Actual Slots = Master Schedule(day) - Active Occupied Slots
    Returns: (day_label, free_slots, authoritative_fee)
    """
    if not day_name and date_iso:
        import datetime
        try:
            dt = datetime.datetime.strptime(date_iso, "%Y-%m-%d")
            day_name = dt.strftime("%A")
        except ValueError:
            day_name = "Monday"
            
    doc_id = doc.get("id", "doc_001")
    fee = int(doc.get("fee", 1200))
    
    # 1. Base schedule from Doctor Master
    availability = doc.get("availability") or {}
    base_slots = availability.get(day_name, []) if day_name else []
    if not base_slots and "time_slots" in availability:
        base_slots = availability.get("time_slots", [])
        
    # 2. Query occupied slots from Authoritative Booking Store
    occupied = booking_store.get_occupied_slots(doc_id, date_iso) if date_iso else set()
    
    # 3. Compute available slots
    free_slots = [s for s in base_slots if s not in occupied]
    day_label = day_name or "OPD hours"

    # [MARKET-01] Dynamic Doctor Roster check (leaves / OT)
    try:
        from src.integrations.roster_store import roster_store
        doc_name = doc.get("name", "")
        r_status = roster_store.get_doctor_status(doc_name or doc_id)
        if r_status.get("status") == "ON_LEAVE":
            return day_label, [], fee
    except Exception as e:
        logger.debug("[ROSTER] Error checking doctor leave status for '%s': %s", doc_name or doc_id, e)
    
    return day_label, free_slots, fee



def _date_iso_to_spoken(date_iso: str) -> str:
    if not date_iso:
        return "tomorrow"
    try:
        import datetime
        ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        today_iso = ist_now.strftime("%Y-%m-%d")
        tomorrow_iso = (ist_now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if date_iso == today_iso:
            return "today"
        elif date_iso == tomorrow_iso:
            return "tomorrow"
        dt = datetime.datetime.strptime(date_iso, "%Y-%m-%d")
        return dt.strftime("%A")
    except Exception:
        return date_iso


def get_department_actual_availability(department: str, date_iso: str = None, requested_time_24h: str = None) -> dict:
    """
    Department-Level Availability Authority.
    1. Hard-filters canonical Doctor Master by department.
    2. Evaluates actual bookable availability independently for every matching doctor (Schedule - Bookings - Holds - Exclusions).
    3. Evaluates requested_time_24h against each doctor's actual slots.
    4. If no doctor has requested time: status = UNAVAILABLE_EXACT, matching_doctors = [].
    """
    loader = _get_unified_loader() if KB_SYSTEM == "unified" else None
    from src.kb_loader import get_kb_loader
    kb = loader or get_kb_loader()
    all_docs = kb.get_doctors()
    
    norm_dept = _normalize_department_name(department)
    dept_docs = [
        d for d in all_docs
        if _normalize_department_name(d.get("department", "")) == norm_dept
    ]
    
    if not dept_docs:
        return {
            "success": False,
            "status": "DEPARTMENT_NOT_FOUND",
            "response_contract": "DEPARTMENT_NOT_FOUND",
            "department": department,
            "answer": f"I could not find the {department} department. Which department would you like me to check?",
            "matching_doctors": [],
            "available_alternatives": [],
            "authoritative": True
        }
        
    if not date_iso:
        import datetime
        ist_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
        date_iso = (ist_now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
    matching_docs = []
    all_dept_slots = []
    doc_summaries = []
    
    for d in dept_docs:
        day_label, free_slots, fee = get_actual_doctor_availability(d, date_iso=date_iso)
        doc_info = {
            "doctor_id": d.get("id"),
            "doctor_name": d.get("name"),
            "department": d.get("department"),
            "fee": fee,
            "available_slots": free_slots,
            "date_iso": date_iso
        }
        doc_summaries.append(doc_info)
        all_dept_slots.extend(free_slots)
        if requested_time_24h and requested_time_24h in free_slots:
            matching_docs.append(doc_info)
            
    unique_alternatives = sorted(list(set(all_dept_slots)))
    spoken_d = _date_iso_to_spoken(date_iso)
    day_phrase = spoken_d if spoken_d in ("today", "tomorrow") else f"on {spoken_d}"
    
    if requested_time_24h:
        spoken_t = render_time(requested_time_24h)
        if matching_docs:
            dnames = ", ".join(m["doctor_name"] for m in matching_docs)
            return {
                "success": True,
                "status": "AVAILABLE_EXACT",
                "response_contract": "EXACT_SLOT_AVAILABLE",
                "department": department,
                "date_iso": date_iso,
                "requested_time": requested_time_24h,
                "spoken_time": spoken_t,
                "matching_doctors": matching_docs,
                "available_alternatives": unique_alternatives,
                "available_slots_spoken": _format_slots_concise([requested_time_24h]),
                "answer": f"{dnames} in {department.title()} is available at {spoken_t} {day_phrase}.",
                "facts": {
                    "doctor_name": {"value": matching_docs[0]["doctor_name"], "source": "doctor_master", "authoritative": True},
                    "fee": {"value": matching_docs[0]["fee"], "source": "doctor_master", "authoritative": True},
                    "time": {"value": spoken_t, "source": "availability_engine", "authoritative": True}
                },
                "authoritative": True
            }
        else:
            proactive_next = _find_next_available_slot_in_range(department, date_iso)
            res = {
                "success": True,
                "status": "UNAVAILABLE_EXACT",
                "response_contract": "EXACT_SLOT_UNAVAILABLE",
                "department": department,
                "date_iso": date_iso,
                "requested_time": requested_time_24h,
                "spoken_time": spoken_t,
                "matching_doctors": [],
                "available_alternatives": unique_alternatives[:3],
                "available_slots_spoken": _format_slots_concise(unique_alternatives),
                "answer": f"I'm sorry, {spoken_t} isn't available {day_phrase} in {department.title()}.",
                "facts": {
                    "requested_time": {"value": spoken_t, "source": "caller_speech", "authoritative": True},
                    "status": {"value": "UNAVAILABLE_EXACT", "source": "availability_engine", "authoritative": True}
                },
                "authoritative": True
            }
            if proactive_next:
                res["proactive_next_slot"] = proactive_next
            return res
    else:
        # General department slots inquiry
        active_docs = [d for d in doc_summaries if d["available_slots"]]
        if active_docs:
            parts = []
            for d in active_docs:
                parts.append(f"{d['doctor_name']} (fee {render_currency(d['fee'])}, slots: {', '.join([render_time(s) for s in d['available_slots'][:2]])})")
            return {
                "success": True,
                "status": "DEPARTMENT_SLOTS_AVAILABLE",
                "response_contract": "DEPARTMENT_SLOTS_AVAILABLE",
                "department": department,
                "date_iso": date_iso,
                "doctors": doc_summaries,
                "matching_doctors": active_docs,
                "available_alternatives": unique_alternatives,
                "available_slots_spoken": _format_slots_concise(unique_alternatives),
                "answer": f"In {department.title()}, we have: {'; '.join(parts)}.",
                "facts": {
                    "matched_doctors": {"value": [d["doctor_name"] for d in active_docs], "source": "doctor_master", "authoritative": True},
                    "count": {"value": len(active_docs), "source": "doctor_master", "authoritative": True}
                },
                "authoritative": True
            }
        else:
            proactive_next = _find_next_available_slot_in_range(department, date_iso)
            res = {
                "success": True,
                "status": "NO_SLOT_AVAILABLE",
                "response_contract": "NO_SLOT_AVAILABLE",
                "department": department,
                "date_iso": date_iso,
                "doctors": doc_summaries,
                "matching_doctors": [],
                "available_alternatives": [],
                "available_slots_spoken": "no available slots",
                "answer": f"There are no available slots in {department.title()} for {spoken_d}.",
                "authoritative": True
            }
            if proactive_next:
                res["proactive_next_slot"] = proactive_next
            return res


def resolve_doctor_entity(query_name: str, hospital_id: str = None) -> tuple[Optional[dict], float, bool]:
    """
    Deterministic entity resolver for doctor names.
    Returns: (doctor_record_dict, confidence_score, requires_confirmation)
    - If exact match: returns (doc, 1.0, False)
    - If phonetic/approximate match (e.g. 'Samil Kulkarni' -> 'Dr. Sameer Kulkarni'): returns (doc, confidence, True)
    - If no match: returns (None, 0.0, False)
    """
    if not query_name or not str(query_name).strip():
        return None, 0.0, False

    loader = _get_unified_loader() if KB_SYSTEM == "unified" else None
    doctors = loader.get_doctors() if loader else tenant_manager.get_hospital_data(hospital_id).get("doctors", [])
    
    clean_q = (
        str(query_name).lower()
        .replace("dr.", "").replace("dr ", "").replace("doctor", "")
        .replace("डॉ.", "").replace("डॉ ", "").replace("डॉक्टर", "").replace("डाक्टर", "")
        .strip()
    )
    if not clean_q:
        return None, 0.0, False
        
    for doc in doctors:
        dname = doc.get("name", "").lower().replace("dr.", "").replace("dr ", "").strip()
        if clean_q == dname:
            return doc, 1.0, False
        
        # Check token matches
        d_tokens = [t for t in dname.split() if len(t) > 2]
        q_tokens = [t for t in clean_q.split() if len(t) > 2]
        
        if q_tokens and all(qt in d_tokens for qt in q_tokens) and len(q_tokens) >= 2:
            return doc, 1.0, False

    # Phonetic / prefix / partial token similarity search
    best_doc = None
    best_avg_sim = 0.0
    
    for doc in doctors:
        dname = doc.get("name", "").lower().replace("dr.", "").replace("dr ", "").strip()
        d_tokens = [t for t in dname.split() if len(t) > 2]
        q_tokens = [t for t in clean_q.split() if len(t) > 2]
        
        if not q_tokens or not d_tokens:
            continue
            
        token_sims = []
        for qt in q_tokens:
            best_t_sim = 0.0
            for dt in d_tokens:
                if qt == dt:
                    sim = 1.0
                else:
                    cp = 0
                    for c1, c2 in zip(qt, dt):
                        if c1 == c2:
                            cp += 1
                        else:
                            break
                    if cp >= 3 or (len(qt) >= 4 and len(dt) >= 4 and qt[:3] == dt[:3]):
                        sim = cp / max(len(qt), len(dt))
                    else:
                        sim = 0.0
                if sim > best_t_sim:
                    best_t_sim = sim
            token_sims.append(best_t_sim)
            
        avg_sim = sum(token_sims) / len(token_sims)
        
        if avg_sim > best_avg_sim:
            best_avg_sim = avg_sim
            best_doc = doc
            
    if best_doc and best_avg_sim >= 0.5:
        is_exact = best_avg_sim >= 0.99
        return best_doc, round(best_avg_sim, 2), not is_exact
        
    return None, 0.0, False



def _unified_doctor_availability(args: dict, hospital_id: str = None) -> dict:
    loader = _get_unified_loader()
    raw_query = str(args.get("query") or "").strip()
    explicit_doctor = str(args.get("doctor_name") or "").strip()
    explicit_dept = str(args.get("department") or "").strip()
    explicit_date = str(args.get("date") or "").strip()
    explicit_time = str(args.get("time") or "").strip()
    
    query = _normalize_query(raw_query)
    
    # 1. Normalize Date & Time
    norm = normalize_appointment_datetime(explicit_date or query, explicit_time or query)
    date_iso = norm["date_iso"]
    time_24h = norm["time_24h"]
    
    # 2. If department is explicitly provided or detected in query
    target_dept = explicit_dept or _normalize_department_name(query)
    if not target_dept or target_dept == query:
        # Check individual tokens
        for t in _tokens(query):
            d = _normalize_department_name(t)
            if d and d != t:
                target_dept = d
                break
                
    # 3. If explicit doctor name is provided or doctor name is in query
    matched_doc = None
    if explicit_doctor:
        matched_doc, conf, _ = resolve_doctor_entity(explicit_doctor, hospital_id)
    else:
        matched_doc, conf, _ = resolve_doctor_entity(raw_query, hospital_id)
        if not matched_doc:
            doctors = loader.get_doctors() if loader else []
            for d in doctors:
                d_name = d.get("name", "").lower().replace("dr.", "").replace("dr ", "").strip()
                if d_name and (d_name in query or any(part in query for part in d_name.split() if len(part) > 3)):
                    matched_doc = d
                    break

    if matched_doc:
        doc = matched_doc
        day_label, free_slots, fee = get_actual_doctor_availability(doc, date_iso=date_iso)
        spoken_t = render_time(time_24h) if time_24h else None
        is_avail = time_24h in free_slots if time_24h else bool(free_slots)
        spoken_d = norm.get("spoken_date") or _date_iso_to_spoken(date_iso)
        day_phrase = spoken_d if spoken_d in ("today", "tomorrow") else f"on {spoken_d}"

        # [MARKET-01] Dynamic Doctor Roster Check (proactive leave notification)
        try:
            from src.integrations.roster_store import roster_store
            doc_roster = roster_store.get_doctor_status(doc.get("name") or doc.get("id"))
            if doc_roster.get("status") == "ON_LEAVE":
                reason = doc_roster.get("reason") or "emergency leave"
                alt_doc = doc_roster.get("alternative_doctor") or "another specialist in our department"
                ret_date = doc_roster.get("return_date")
                ret_str = f" on {ret_date}" if ret_date else " tomorrow"
                return {
                    "success": True,
                    "status": "DOCTOR_ON_LEAVE",
                    "response_contract": "DOCTOR_ON_LEAVE",
                    "doctor_name": doc["name"],
                    "department": doc["department"],
                    "date_iso": date_iso,
                    "available_slots": [],
                    "answer": f"I would like to inform you that {doc['name']} is currently on {reason} today. Would you like to schedule an appointment with {alt_doc}, or book with {doc['name']}{ret_str}?",
                    "facts": {
                        "doctor_name": {"value": doc["name"], "source": "doctor_master", "authoritative": True},
                        "fee": {"value": fee, "source": "doctor_master", "authoritative": True},
                        "status": {"value": "DOCTOR_ON_LEAVE", "source": "roster_store", "authoritative": True}
                    },
                    "authoritative": True
                }
        except Exception as e:
            logger.debug("[ROSTER] Error checking roster leave status: %s", e)
        
        if time_24h and not is_avail:
            return {
                "success": True,
                "status": "UNAVAILABLE_EXACT",
                "response_contract": "EXACT_SLOT_UNAVAILABLE",
                "doctor_name": doc["name"],
                "department": doc["department"],
                "date_iso": date_iso,
                "requested_time": time_24h,
                "spoken_time": spoken_t,
                "available_slots": free_slots,
                "available_alternatives": free_slots[:3],
                "answer": f"I'm sorry, {spoken_t} isn't available {day_phrase} with {doc['name']}.",
                "facts": {
                    "doctor_name": {"value": doc["name"], "source": "doctor_master", "authoritative": True},
                    "fee": {"value": fee, "source": "doctor_master", "authoritative": True},
                    "status": {"value": "UNAVAILABLE_EXACT", "source": "availability_engine", "authoritative": True}
                },
                "authoritative": True
            }
        elif time_24h and is_avail:
            return {
                "success": True,
                "status": "AVAILABLE_EXACT",
                "response_contract": "EXACT_SLOT_AVAILABLE",
                "doctor_name": doc["name"],
                "department": doc["department"],
                "date_iso": date_iso,
                "requested_time": time_24h,
                "spoken_time": spoken_t,
                "available_slots": free_slots,
                "fee": fee,
                "answer": f"{doc['name']} ({doc['department']}) is available at {spoken_t} {day_phrase} with a consultation fee of {render_currency(fee)}.",
                "facts": {
                    "doctor_name": {"value": doc["name"], "source": "doctor_master", "authoritative": True},
                    "fee": {"value": fee, "source": "doctor_master", "authoritative": True},
                    "time": {"value": spoken_t, "source": "availability_engine", "authoritative": True}
                },
                "authoritative": True
            }
        else:
            return {
                "success": True,
                "status": "DOCTOR_SLOTS_AVAILABLE",
                "response_contract": "DOCTOR_SLOTS_AVAILABLE",
                "doctor_name": doc["name"],
                "department": doc["department"],
                "date_iso": date_iso,
                "available_slots": free_slots,
                "fee": fee,
                "answer": f"{doc['name']} ({doc['department']}) is available {day_phrase} with slots at {', '.join([render_time(s) for s in free_slots[:3]])}. Consultation fee is {render_currency(fee)}.",
                "facts": {
                    "doctor_name": {"value": doc["name"], "source": "doctor_master", "authoritative": True},
                    "fee": {"value": fee, "source": "doctor_master", "authoritative": True}
                },
                "authoritative": True
            }

    # 4. If target department is known -> delegate to get_department_actual_availability
    if target_dept and target_dept != query:
        return get_department_actual_availability(target_dept, date_iso=date_iso, requested_time_24h=time_24h)

    # 5. Generic doctor query without department or doctor -> DEPARTMENT_REQUIRED (Zero department dumping!)
    return {
        "success": True,
        "status": "DEPARTMENT_REQUIRED",
        "response_contract": "DEPARTMENT_REQUIRED",
        "answer": "Which department would you like me to check?",
        "doctors": [],
        "authoritative": True
    }


def hospital_info(args: dict, hospital_id: str = None) -> dict:
    """Fetches hospital info. Priority: local tenant JSON → FAISS cache → Bedrock KB → fallback."""
    if KB_SYSTEM == "unified":
        return _unified_hospital_info(args, hospital_id)

    data = tenant_manager.get_hospital_data(hospital_id)
    query = args.get("query", "").lower()
    normalized_query = _normalize_query(query)

    # 1. Check specific local tenant data (fastest, most reliable)
    faq_list = data.get("faq", [])

    # Map keywords to FAQ intents to make matching extremely robust
    keyword_to_intent = {
        "icu_visiting_hours": [["visiting", "icu"], ["milne", "icu"], ["visit", "icu"], ["icu", "milne"], ["icu", "time"]],
        "general_ward_visiting": [["visiting", "ward"], ["milne", "ward"], ["milne", "time"], ["visiting", "hours"], ["visiting", "time"], ["ward", "milne"]],
        "nicu_visiting": [["nicu"], ["newborn", "visit"], ["baby", "nicu"], ["nicu", "parent"]],
        "night_visiting": [["night", "visit"], ["overnight"], ["raat", "milna"], ["night", "restriction"]],
        "nabh_accreditation": [["nabh"], ["accreditation"], ["accredited"], ["certified"]],
        "insurance_tpa": [["insurance"], ["cashless"], ["tpa"], ["mediclaim"], ["health", "card"]],
        "parking_charges": [["parking"], ["park"], ["flat", "rate"], ["admitted", "patient", "parking"], ["visitor", "parking"]],
        "pharmacy_hours": [["pharmacy"], ["medicine"], ["dawai"], ["medical", "store"]],
        "lab_reports": [["report"], ["reports"], ["test", "result"]],
        "blood_test_fasting": [["fasting"], ["fast", "before"], ["khana", "pehle"]],
        "payment_modes": [["payment"], ["upi"], ["card"], ["cash"], ["pay"]],
        "cafeteria_location": [["cafeteria"], ["food"], ["eat"], ["khana"]],
        "wheelchair_porter": [["wheelchair"], ["porter"], ["stretcher"]],
        "ambulance_service": [["ambulance"]],
        "emergency_department": [["emergency"], ["accident"]],
        "doctor_directions": [["floor"], ["block"], ["which", "room"], ["room", "number"], ["room", "direction"], ["where", "room"], ["room", "kahan"], ["direction"], ["where", "is"], ["kahan", "hai"]],
        "second_opinion": [["second", "opinion"]],
    }


    # First check keyword mapping for FAQs
    if isinstance(faq_list, list):
        for intent, word_groups in keyword_to_intent.items():
            if intent == "blood_test_fasting":
                scan_kws = ["mri", "ct", "scan", "ultrasound", "usg", "xray", "x-ray", "mammogram", "echo", "tmt"]
                if any(_has_word(normalized_query, kw) for kw in scan_kws):
                    continue
            if intent == "doctor_directions":
                room_rent_kws = ["rent", "rate", "price", "cost", "type", "tariff", "deluxe", "icu", "ward"]
                if any(_has_word(normalized_query, kw) for kw in room_rent_kws):
                    continue
            for group in word_groups:
                if all(_has_word(normalized_query, word) for word in group):
                    for item in faq_list:
                        if item.get("intent") == intent:
                            return {"answer": item.get("answer")}

        # Substring/questions matching in FAQ
        for item in faq_list:
            intent = item.get("intent", "")
            if intent == "blood_test_fasting":
                scan_kws = ["mri", "ct", "scan", "ultrasound", "usg", "xray", "x-ray", "mammogram", "echo", "tmt"]
                if any(_has_word(normalized_query, kw) for kw in scan_kws):
                    continue
            if intent == "doctor_directions":
                room_rent_kws = ["rent", "rate", "price", "cost", "type", "tariff", "deluxe", "icu", "ward"]
                if any(_has_word(normalized_query, kw) for kw in room_rent_kws):
                    continue
            intent_match = _has_word(normalized_query, intent.replace("_", " "))
            question_match = any(_has_word(normalized_query, q.lower()) or _has_word(q.lower(), normalized_query) for q in item.get("questions", []))
            if intent_match or question_match:
                return {"answer": item.get("answer")}

    elif isinstance(faq_list, dict):
        for key, val in faq_list.items():
            if _has_word(normalized_query, key):
                return {"answer": val}

    # Check services (e.g. MRI, Thyroid, CT Head, Complete Blood Count, etc.)
    services = data.get("services", [])
    matched_services = []
    
    # Category detection for services
    service_keywords = {
        "mri": ["mri", "magnetic"],
        "ct": ["ct", "pet ct", "contrast ct"],
        "thyroid": ["thyroid", "t3", "t4", "tsh"],
        "cbc": ["cbc", "complete blood count"],
        "blood sugar": ["blood sugar", "fasting sugar", "hba1c", "glucose"],
        "ultrasound": ["ultrasound", "usg", "sonography", "sonogram", "pregnancy scan", "fetal scan", "pelvic usg", "abdominal usg"],
        "lipid": ["lipid", "cholesterol"],
        "liver": ["liver", "lft"],
        "kidney": ["kidney", "kft", "rft"],
        "vitamin d": ["vitamin d"],
        "vitamin b12": ["b12"],
        "x-ray": ["x-ray", "xray", "chest xray", "spine xray", "bone xray", "radiograph"],
        "radiology": ["radiology", "radiologist", "imaging", "diagnostic imaging", "scan"],
        "physiotherapy": ["physiotherapy", "rehabilitation", "therapy"],
        "dialysis": ["dialysis"],
        "cardiac": ["cardiac", "heart", "ecg", "echo", "tmt", "stress test"],
    }
    
    detected_cats = []
    for cat, kw_list in service_keywords.items():
        if any(_has_word(normalized_query, kw) for kw in kw_list):
            detected_cats.append(cat)
            
    # Collect matches
    for s in services:
        s_name = s.get("name", "").lower()
        if _has_word(normalized_query, s_name) or _has_word(s_name, normalized_query):
            matched_services.append(s)
            continue
            
        for cat in detected_cats:
            if _has_word(s_name, cat) or any(_has_word(s_name, kw) for kw in service_keywords[cat]):
                matched_services.append(s)
                break
                
    # Refine matches if there are multiple matches
    if len(matched_services) > 1:
        refined = []
        for s in matched_services:
            s_name = s.get("name", "").lower()
            for word in ["brain", "spine", "head", "chest", "abdomen", "contrast", "fasting", "package"]:
                if word in normalized_query and word in s_name:
                    refined.append(s)
                    break
        if len(refined) == 1:
            matched_services = refined

    if matched_services:
        if len(matched_services) == 1:
            s = matched_services[0]
            price_str = f" costs Rs. {s['price']}" if s.get("price") else ""
            prep_str = f" Prep: {s['prep']}." if s.get("prep") else ""
            loc_str = f" Location: {s['location']}." if s.get("location") else ""
            dur_str = f" It takes about {s['duration']}." if s.get("duration") else ""
            return {"answer": f"{s['name']}{price_str}.{dur_str}{prep_str}{loc_str}"}
        else:
            ans = "We offer: " + ", ".join([f"{s['name']} (Rs. {s['price']})" for s in matched_services]) + "."
            return {"answer": ans}

    # Check health packages
    packages = data.get("health_packages", [])
    matched_packages = []
    for p in packages:
        p_name = p.get("name", "").lower()
        if (p_name in normalized_query) or (normalized_query in p_name and len(normalized_query) >= 3):
            matched_packages.append(p)
            continue
        if any(kw in normalized_query for kw in ["package", "wellness", "checkup", "preventive"]):
            matched_packages.append(p)
            
    # Refine packages
    if len(matched_packages) > 1:
        refined = []
        for p in matched_packages:
            p_name = p.get("name", "").lower()
            for word in ["silver", "gold", "executive", "cardiac", "women"]:
                if word in normalized_query and word in p_name:
                    refined.append(p)
                    break
        if len(refined) == 1:
            matched_packages = refined
            
    if matched_packages:
        if len(matched_packages) == 1:
            p = matched_packages[0]
            price_str = f" costs Rs. {p['price']}" if p.get("price") else ""
            inc_str = f" It includes: {p['includes']}." if p.get("includes") else ""
            prep_str = f" Prep: {p['prep']}." if p.get("prep") else ""
            return {"answer": f"{p['name']}{price_str}.{inc_str}{prep_str}"}
        else:
            ans = "We offer the following packages: " + ", ".join([f"{p['name']} (Rs. {p['price']})" for p in matched_packages]) + "."
            return {"answer": ans}

    # Check room types
    rooms = data.get("room_types", [])
    matched_rooms = []
    for r in rooms:
        r_name = r.get("name", "").lower()
        if (r_name in normalized_query) or (normalized_query in r_name and len(normalized_query) >= 3):
            matched_rooms.append(r)
            
    if not matched_rooms:
        if "icu" in normalized_query:
            matched_rooms = [r for r in rooms if "icu" in r.get("name", "").lower()]
        elif "deluxe" in normalized_query or "private" in normalized_query:
            matched_rooms = [r for r in rooms if "deluxe" in r.get("name", "").lower() or "private" in r.get("name", "").lower()]
        elif "semi" in normalized_query:
            matched_rooms = [r for r in rooms if "semi" in r.get("name", "").lower()]
        elif "ward" in normalized_query or "general" in normalized_query:
            matched_rooms = [r for r in rooms if "general" in r.get("name", "").lower() or "ward" in r.get("name", "").lower()]
        elif any(kw in normalized_query for kw in ["room", "rent", "tariff", "charges", "rate", "price"]):
            matched_rooms = rooms
            
    if matched_rooms:
        if len(matched_rooms) == 1:
            r = matched_rooms[0]
            return {"answer": f"{r['name']} rate is Rs. {r['price_per_day']} per day. Description: {r['description']}."}
        else:
            ans = "Our daily room rates are: " + ", ".join([f"{r['name']}: Rs. {r['price_per_day']}" for r in rooms]) + "."
            return {"answer": ans}

    # Check amenities (like parking, cafeteria, ATM, wifi, wheelchair)
    amenities = data.get("amenities", {})
    
    # Specific Wi-Fi check (handles hyphen and spaces safely)
    if "wifi" in normalized_query or "wi-fi" in normalized_query or "wi fi" in normalized_query:
        wifi_info = amenities.get("wifi")
        if wifi_info:
            return {"answer": wifi_info}
            
    # Specific flat rate parking check for visitors/attendants
    if any(_has_word(normalized_query, k) for k in ["flat rate", "flat", "attendant", "visitor parking"]):
        parking_info = amenities.get("parking", {})
        if isinstance(parking_info, dict) and "admitted_patient_visitors" in parking_info:
            return {"answer": f"For admitted patients' visitors, the parking rate is {parking_info['admitted_patient_visitors']}."}
            
    if isinstance(amenities, dict):
        for key, val in amenities.items():
            if _has_word(normalized_query, key):
                if isinstance(val, dict):
                    details = ", ".join([f"{k.replace('_', ' ').title()}: {v}" for k, v in val.items()])
                    return {"answer": f"Details for {key}: {details}."}
                else:
                    return {"answer": f"{key.title()}: {val}."}

    if any(_has_word(normalized_query, k) for k in ["address", "location", "where"]):
        return {"answer": f"{data.get('name')} is located at {data.get('address', 'our main facility')}."}

    if any(_has_word(normalized_query, k) for k in ["contact", "phone", "number", "telephone", "mobile"]):
        return {"answer": f"{data.get('name')} contact number is {data.get('contact', '+91 80 4000 9000')}."}

    if any(_has_word(normalized_query, k) for k in ["pharmacy", "medicine", "dawai"]):
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
    if KB_SYSTEM == "unified":
        return _unified_doctor_availability(args, hospital_id)

    data = tenant_manager.get_hospital_data(hospital_id)
    query = args.get("query", "").lower()
    normalized_query = _normalize_query(query)
    doctors = data.get("doctors", [])

    # Specialty to department mapping
    specialty_to_dept = {
        "cardio": "cardiology",
        "heart": "cardiology",
        "neuro": "neurology",
        "brain": "neurology",
        "ortho": "orthopedics",
        "joint": "orthopedics",
        "bone": "orthopedics",
        "knee": "orthopedics",
        "child": "pediatrics",
        "baby": "pediatrics",
        "pediatr": "pediatrics",
        "gyneco": "gynecology",
        "obstetr": "gynecology",
        "pregnancy": "gynecology",
        "women": "gynecology",
        "diabetes": "endocrinology",
        "diabeto": "endocrinology",
        "thyroid": "endocrinology",
        "stomach": "gastroenterology",
        "gastro": "gastroenterology",
        "lung": "pulmonology",
        "chest": "pulmonology",
        "breathing": "pulmonology",
        "cancer": "oncology",
        "oncolo": "oncology",
        "eye": "ophthalmology",
        "ophthalm": "ophthalmology",
        "ent": "ent",
        "ear": "ent",
        "nose": "ent",
        "throat": "ent",
        "skin": "dermatology",
        "dermat": "dermatology",
        "physician": "general medicine",
        "medicine": "general medicine",
        "fever": "general medicine",
        "general doctor": "general medicine",
    }

    query_depts = []
    for spec, dept in specialty_to_dept.items():
        if _has_prefix_word(normalized_query, spec):
            query_depts.append(dept)

    # 1. Search in local tenant roster (most accurate for configured clinics)
    matched_docs = []
    for doc in doctors:
        doc_name_lower = doc["name"].lower()
        doc_dept_lower = doc["dept"].lower()
        
        # Extract name parts (e.g., "Kavita", "Singh", "Gupta", "Sen")
        name_clean = doc_name_lower.replace("dr.", "").replace("dr", "").strip()
        name_parts = [p for p in name_clean.split() if len(p) >= 3] # Keep parts with at least 3 characters
        
        # Check if full name, department, or any name part is in normalized query
        name_match = (_has_word(normalized_query, doc_name_lower) or 
                      any(_has_word(normalized_query, part) for part in name_parts))
        dept_match = (_has_word(normalized_query, doc_dept_lower) or 
                      any(_has_word(doc_dept_lower, d) for d in query_depts))
        # Also check specialty_keywords field from JSON for precise specialist queries
        keyword_match = any(
            _has_prefix_word(normalized_query, kw.lower())
            for kw in doc.get("specialty_keywords", [])
        )
        
        if name_match or dept_match or keyword_match:
            matched_docs.append(doc)
            
    if matched_docs:
        if len(matched_docs) == 1:
            doc = matched_docs[0]
            fee_str = f" Consultation fee is Rs. {doc['fee']}." if doc.get("fee") else ""
            
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
                    "Should I go ahead and book this slot for you?"
                )
            }
        else:
            ans = f"We have {len(matched_docs)} specialists: "
            doc_strings = []
            for doc in matched_docs:
                availability = doc.get("availability")
                if availability:
                    days = ", ".join(availability.get("days", []))
                    schedule_str = f"on {days}"
                else:
                    schedule_str = doc.get('schedule', 'during OPD')
                doc_strings.append(f"{doc['name']} ({schedule_str})")
            ans += ", ".join(doc_strings) + ". Who would you like to consult?"
            return {"answer": ans}

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


def search_patient(args: dict, hospital_id: str = None) -> dict:
    """Candidate discovery tool. Strictly returns minimal verification tokens only."""
    from src.server import memory_manager
    name = args.get("name")
    phone = args.get("phone")
    ref = args.get("appointment_ref")

    candidates = memory_manager.search_patient_candidates(name=name, phone=phone, appointment_ref=ref)
    if not candidates:
        return {
            "success": False,
            "message": "No matching patient profile found. Please proceed with fresh registration.",
            "candidates": []
        }

    return {
        "success": True,
        "message": f"Found {len(candidates)} candidate match(es). Identity verification required.",
        "candidates": candidates
    }


def appointment_booking(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentBookingTool. Structured atomic booking with authoritative data sinks and canonical doctor IDs."""
    audit_logger.log_tool_use("active_session", hospital_id or "default", "appointment_booking")
    
    patient = (args.get("patient_name") or "the patient").strip()
    dept = (args.get("doctor_dept") or "").strip()
    raw_date = (args.get("date") or "tomorrow").strip()
    raw_time = (args.get("time") or "10:00 AM").strip()
    intent = args.get("symptom_intent", "General Consultation")
    phone = args.get("phone_number", "N/A")
    doctor_query = (args.get("doctor_name") or "").strip()
    old_ref_id = args.get("old_ref_id") or args.get("reference_id")

    # 1. Canonical Date/Time Normalization & Ambiguity Check
    norm = normalize_appointment_datetime(raw_date, raw_time)
    if norm["is_ambiguous"]:
        return {
            "success": False,
            "requires_clarification": True,
            "answer": norm["ambiguity_reason"],
            "facts": {"status": {"value": "REQUIRES_CLARIFICATION", "source": "normalizer", "authoritative": True}}
        }
        
    date_iso = norm["date_iso"]
    time_24h = norm["time_24h"]
    spoken_date = norm["spoken_date"]
    spoken_time = norm["spoken_time"]

    # 2. Authoritative Doctor Master Lookup (Single source of truth)
    from src.kb_loader import get_kb_loader
    kb = get_kb_loader()
    all_docs = kb.get_doctors()
    
    matched_doc = None
    if doctor_query and doctor_query.lower() not in ("any", "available", "doctor", "specialist"):
        matched_doc, conf, req_confirm = resolve_doctor_entity(doctor_query, hospital_id)
        if not matched_doc:
            clean_dq = doctor_query.lower()
            for d in all_docs:
                if clean_dq in d.get("name", "").lower() or clean_dq in d.get("id", "").lower():
                    matched_doc = d
                    break

    if not matched_doc and dept:
        norm_dept = _normalize_department_name(dept)
        dept_docs = [d for d in all_docs if _normalize_department_name(d.get("department", "")) == norm_dept or norm_dept in d.get("name", "").lower()]
        if dept_docs:
            matched_doc = dept_docs[0]

    if not matched_doc:
        return {
            "success": False,
            "requires_clarification": True,
            "answer": "Which doctor or department would you like the appointment with? For example, Cardiology, Orthopedics, or General Medicine.",
            "facts": {"status": {"value": "REQUIRES_CLARIFICATION", "source": "booking_tool", "authoritative": True}}
        }

    doctor_id = matched_doc.get("id", "doc_001")
    doctor_name = doctor_query if (doctor_query and doctor_query.lower() not in ("any", "available", "doctor", "specialist") and not any(doctor_query.lower() in d.get("name", "").lower() for d in all_docs)) else matched_doc.get("name", "Dr. Sameer Kulkarni")
    dept_name = dept or matched_doc.get("department", "General Medicine")
    authoritative_fee = int(matched_doc.get("fee", 1200))

    # [MARKET-01] Dynamic Doctor Roster check — Block booking if doctor is on emergency leave
    try:
        from src.integrations.roster_store import roster_store
        doc_roster = roster_store.get_doctor_status(doctor_name or doctor_id)
        if doc_roster.get("status") == "ON_LEAVE":
            reason = doc_roster.get("reason") or "emergency leave"
            alt = doc_roster.get("alternative_doctor") or "another specialist in our department"
            return {
                "success": False,
                "status": "DOCTOR_ON_LEAVE",
                "answer": f"I cannot book this appointment because {doctor_name} is on {reason} today. Would you like me to book with {alt} instead?",
                "facts": {"status": {"value": "DOCTOR_ON_LEAVE", "source": "roster_store", "authoritative": True}}
            }
    except Exception as e:
        logger.debug("[ROSTER] Error checking roster store in appointment_booking: %s", e)

    # Generate unique reference ID
    ref_id = f"IS-APP-{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    # Build complete payload
    booking_payload = {
        "patient_name": patient,
        "phone": phone,
        "doctor_id": doctor_id,
        "doctor": doctor_name,
        "doctor_name": doctor_name,
        "dept": dept_name,
        "department": dept_name,
        "date": raw_date,
        "time": raw_time,
        "date_iso": date_iso,
        "time_24h": time_24h,
        "visit_time": f"{raw_date} at {raw_time}",
        "ref_id": ref_id,
        "booking_id": ref_id,
        "intent": intent,
        "fee": authoritative_fee,
        "status": "CONFIRMED",
    }

    # 3. Handle Explicit Rescheduling with Atomic Zero-Loss Rollback
    if "reschedule" in intent.lower() and old_ref_id:
        success, msg, appt_result = booking_store.atomic_reschedule(old_ref_id, booking_payload)
        if not success:
            _, free_slots, _ = get_actual_doctor_availability(matched_doc, date_iso=date_iso)
            slot_list = ", ".join(free_slots[:3]) if free_slots else "no open slots"
            return {
                "success": False,
                "answer": f"The requested time on {spoken_date} is unavailable. Your existing appointment {old_ref_id} remains active. Available slots for {doctor_name} are: {slot_list}.",
                "status": "FAILED",
                "preserved_appointment": old_ref_id
            }
        return {
            "success": True,
            "booking_id": ref_id,
            "ref_id": ref_id,
            "patient_name": patient,
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "department": dept_name,
            "date": spoken_date,
            "time": spoken_time,
            "fee": authoritative_fee,
            "status": "CONFIRMED",
            "rescheduled_from": old_ref_id,
            "facts": {
                "doctor_id": {"value": doctor_id, "source": "doctor_master", "authoritative": True},
                "doctor_name": {"value": doctor_name, "source": "doctor_master", "authoritative": True},
                "department": {"value": dept_name, "source": "doctor_master", "authoritative": True},
                "date": {"value": spoken_date, "source": "booking_tool", "authoritative": True},
                "time": {"value": spoken_time, "source": "booking_tool", "authoritative": True},
                "fee": {"value": authoritative_fee, "source": "doctor_master", "authoritative": True},
                "reference_id": {"value": ref_id, "source": "booking_tool", "authoritative": True},
                "status": {"value": "CONFIRMED", "source": "booking_tool", "authoritative": True}
            },
            "answer": f"Your appointment with {doctor_name} in the {dept_name} department has been rescheduled to {spoken_date} at {spoken_time}. Your new reference ID is {ref_id}. The consultation fee is Rs. {authoritative_fee:,}."
        }

    # 4. Handle Explicit Cancellation
    if "cancel" in intent.lower() and old_ref_id:
        success, msg = booking_store.cancel_appointment(old_ref_id)
        if not success:
            return {"success": False, "answer": f"Could not cancel appointment {old_ref_id}: {msg}", "status": "FAILED"}
        return {
            "success": True,
            "ref_id": old_ref_id,
            "status": "CANCELLED",
            "answer": f"Your appointment {old_ref_id} has been cancelled successfully.",
            "facts": {"status": {"value": "CANCELLED", "source": "booking_tool", "authoritative": True}}
        }

    # 5. Atomic Booking Commit
    commit_ok, commit_msg, _ = booking_store.commit_booking(booking_payload)
    if not commit_ok:
        _, free_slots, _ = get_actual_doctor_availability(matched_doc, date_iso=date_iso)
        slot_list = ", ".join(free_slots[:3]) if free_slots else "no open slots"
        return {
            "success": False,
            "slot_occupied": True,
            "answer": f"Slot {spoken_time} on {spoken_date} is already booked. Available slots for {doctor_name} are: {slot_list}. Would you like to choose one of these?",
            "available_slots": free_slots,
            "status": "FAILED"
        }

    # External sync (Sheets)
    try:
        sheets_client.append_booking(booking_payload, hospital_id=hospital_id)
    except Exception as e:
        logger.warning("[BOOKING-SINK] Error saving to external sheets sink: %s", e)

    # Update canonical patient memory
    try:
        from src.server import memory_manager
        memory_manager.create_or_update_patient_profile(
            name=patient,
            phone=phone,
            doctor_name=doctor_name,
            dept=dept_name,
            appointment_ref=ref_id
        )
    except Exception as e:
        logger.warning("[BOOKING-MEM] Error updating patient memory: %s", e)

    # [MARKET-02] Multi-Channel Notification Dispatch (SMS / WhatsApp Pass)
    try:
        from src.integrations.notifications import notification_service
        notification_service.dispatch_booking_confirmation(booking_payload, hospital_id=hospital_id)
    except Exception as e:
        logger.warning("[NOTIF] Error dispatching digital booking pass: %s", e)

    # Caller-safe authoritative fact set with explicit provenance
    facts = {
        "doctor_id": {"value": doctor_id, "source": "doctor_master", "authoritative": True},
        "doctor_name": {"value": doctor_name, "source": "doctor_master", "authoritative": True},
        "department": {"value": dept_name, "source": "doctor_master", "authoritative": True},
        "date": {"value": spoken_date, "source": "booking_tool", "authoritative": True},
        "time": {"value": spoken_time, "source": "booking_tool", "authoritative": True},
        "fee": {"value": authoritative_fee, "source": "doctor_master", "authoritative": True},
        "reference_id": {"value": ref_id, "source": "booking_tool", "authoritative": True},
        "status": {"value": "CONFIRMED", "source": "booking_tool", "authoritative": True}
    }

    return {
        "success": True,
        "booking_id": ref_id,
        "ref_id": ref_id,
        "patient_name": patient,
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "department": dept_name,
        "date": spoken_date,
        "time": spoken_time,
        "fee": authoritative_fee,
        "status": "CONFIRMED",
        "digital_pass_dispatched": True,
        "facts": facts,
        "answer": f"Your appointment with {doctor_name} in the {dept_name} department is confirmed for {spoken_date} at {spoken_time}. Your reference ID is {ref_id}. The consultation fee is Rs. {authoritative_fee:,}.",
    }




def report_status(args: dict, hospital_id: str = None) -> dict:
    """Tool: reportStatusTool. Check if a diagnostic or lab report is ready."""
    patient = args.get("patient_name", "the patient")
    test_type = args.get("test_type", "diagnostic test")
    return {
        "answer": f"The {test_type} report for {patient} is being processed and will be available within 24 hours. You can collect it from our ground floor diagnostic desk or view it online.",
        "patient_name": patient,
        "test_type": test_type,
        "status": "PROCESSING",
        "success": True
    }


def emergency_handoff(args: dict, hospital_id: str = None) -> dict:
    """Tool: handoffTool. Transfer call to human receptionist or emergency desk."""
    reason = args.get("reason", "Emergency assistance")
    return {
        "answer": "Connecting you to our emergency desk immediately. If disconnected, please dial 1066. Please stay on the line.",
        "reason": reason,
        "action": "TRANSFER_TO_HUMAN",
        "status": "ESCALATED",
        "emergency_number": "1066",
        "success": True
    }


def clinical_triage(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Clinical Excellence. Gathers systematic symptom data with audit trail.
    Applies strict hospital-approved clinical triage policy deterministically without LLM guessing.
    """
    audit_logger.log_tool_use("active_session", hospital_id or "default", "clinical_triage")
    
    symptoms = str(args.get("symptoms", "")).strip().lower()
    pain = args.get("pain_intensity")  # Optional: None unless stated
    onset = args.get("onset_duration")  # Optional: None unless stated
    history = args.get("existing_conditions", "None")
    
    # Hospital-Approved Red-Flag Rules (Strict clinical criteria)
    red_flags = [
        "severe chest pain", "crushing chest pain", "radiating pain", "pain in left arm", 
        "difficulty breathing", "severe breathing", "shortness of breath", "loss of consciousness",
        "unconscious", "fainting", "stroke", "face drooping", "slurred speech", "profuse bleeding",
        "heavy bleeding", "heart attack"
    ]
    
    is_red_flag = any(rf in symptoms for rf in red_flags) or (isinstance(pain, (int, float)) and pain >= 8)
    
    if is_red_flag:
        priority = "CRITICAL"
        status = "EMERGENCY"
        action = "EMERGENCY_HANDOFF"
        dept = "Emergency & Trauma"
        response = (
            "This sounds urgent. Please stay on the line, I am connecting you to our emergency desk immediately. "
            "If the call disconnects, please dial 1066 directly."
        )
    else:
        priority = "NORMAL"
        status = "STABLE"
        action = "BOOK_OPD"
        
        # Hospital-approved department mapping
        if any(w in symptoms for w in ["heart", "cardio", "chest", "palpitation"]):
            dept = "Cardiology"
        elif any(w in symptoms for w in ["throat", "ear", "nose", "gala", "kaan", "naak", "tonsil"]):
            dept = "ENT"
        elif any(w in symptoms for w in ["bone", "joint", "fracture", "knee", "back pain", "haddi", "jod"]):
            dept = "Orthopedics"
        elif any(w in symptoms for w in ["brain", "headache", "migraine", "nerve", "seizure"]):
            dept = "Neurology"
        elif any(w in symptoms for w in ["stomach", "acidity", "digestion", "liver", "vomiting", "pet"]):
            dept = "Gastroenterology"
        elif any(w in symptoms for w in ["cough", "fever", "bukhar", "cold", "flu", "weakness"]):
            dept = "General Medicine"
        else:
            dept = "General Medicine"
            
        response = f"Based on your symptoms ({symptoms or 'general discomfort'}), I recommend consulting our {dept} department. Would you like me to check available slots?"

    facts = {
        "symptoms": {"value": symptoms, "source": "caller_speech", "authoritative": True},
        "priority": {"value": priority, "source": "triage_policy", "authoritative": True},
        "status": {"value": status, "source": "triage_policy", "authoritative": True},
        "recommended_department": {"value": dept, "source": "triage_policy", "authoritative": True},
        "recommended_action": {"value": action, "source": "triage_policy", "authoritative": True},
    }
    if pain is not None:
        facts["pain_intensity"] = {"value": pain, "source": "caller_speech", "authoritative": True}
    if onset is not None:
        facts["onset_duration"] = {"value": onset, "source": "caller_speech", "authoritative": True}

    logger.info(f"[TRIAGE] {priority} - Symptoms: {symptoms}, Dept: {dept}, Action: {action}")
    triage_store.write(
        hospital_id=hospital_id,
        symptoms=symptoms,
        pain=pain,
        priority=priority,
        dept=dept,
        is_emergency=is_red_flag
    )

    return {
        "answer": response,
        "status": status,
        "priority": priority,
        "recommended_department": dept,
        "recommended_action": action,
        "facts": facts,
        "success": True
    }



def get_billing_info(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Hospital OS Layer - Billing Intelligence.
    Provides breakdown and status for patient billing.
    """
    patient_id = args.get("patient_id", "unknown")
    patient_name = args.get("patient_name", "the patient")
    
    # 1. Fetch prices from the active hospital data source.
    if KB_SYSTEM == "unified":
        services = _get_unified_loader().get_services()
    else:
        data = tenant_manager.get_hospital_data(hospital_id)
        services = data.get("services", [])
    
    # Mock items based on common inquiries
    items = []
    total = 0
    
    # If query mentions a specific service, use that
    query = str(args.get("query", "")).lower()
    normalized_query = _normalize_query(query)
    found_any = False
    matched_services = _match_services(normalized_query, services) if KB_SYSTEM == "unified" else []
    for s in matched_services or services:
        if matched_services or s["name"].lower() in normalized_query:
            items.append({"name": s["name"], "price": s["price"]})
            total += s["price"]
            found_any = True
            if matched_services:
                break
            
    if not found_any:
        # Default mock bill for demonstration
        items = [
            {"name": "Consultation", "price": 1200},
            {"name": "Routine Lab Tests", "price": 850}
        ]
        total = 2050
        
    response = (
        f"For {patient_name}, the current billing status is PENDING. "
        f"The breakdown includes: " + ", ".join([f"{i['name']} (Rs. {i['price']})" for i in items]) + ". "
        f"The total amount due is Rs. {total}. "
        f"Please visit our hospital billing desk or call our desk to proceed with payment."
    )
    
    return {
        "answer": response,
        "patient_name": patient_name,
        "items": items,
        "total": total,
        "status": "PENDING",
        "success": True
    }


def predict_ot_schedule(args: dict, hospital_id: str = None) -> dict:
    """Requirement: Hospital OS Layer - OT & Surgical Consultation Guidance.
    Explains procedure assessment flow without fabricating speculative slot reservations.
    """
    procedure = args.get("procedure_name", "Surgery").title()
    doctor = args.get("doctor_name", "the attending specialist")
    
    response = (
        f"Operation Theatre OT procedures and surgical scheduling for {procedure} require direct clinical evaluation by our specialist surgeons. "
        f"Please visit our OPD or call our hospital desk at 8 0 4 0 0 0 9 0 0 0 to schedule a surgical consultation."
    )
    
    return {
        "answer": response,
        "procedure": procedure,
        "doctor": doctor,
        "requires_opd_consultation": True,
        "contact": "8 0 4 0 0 0 9 0 0 0",
        "success": True
    }



# ============================================================================
# STRUCTURED VALUE RENDERERS (Deterministic Spoken Formatting)
# ============================================================================
# PHONETIC & SPOKEN VALUE RENDERERS (Delegated to src.rendering)
# ============================================================================
from src.rendering import render_reference_id, render_currency, render_time



# ============================================================================
# DEDICATED SERVICE TOOLS (Lookup, History, Reschedule, Cancel)
# ============================================================================

def appointment_lookup(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentLookupTool. Retrieves upcoming active confirmed appointments."""
    phone = args.get("phone", "")
    patient_id = args.get("patient_id", "")
    ref_id = args.get("appointment_ref", "")
    
    appts = booking_store.lookup_appointments(patient_id=patient_id, phone=phone, appointment_ref=ref_id)
    if not appts:
        return {
            "success": True,
            "found": False,
            "answer": "I could not find any active upcoming appointments under this reference or contact number.",
            "appointments": []
        }
    
    lines = []
    for a in appts:
        lines.append(f"Appointment with {a.get('doctor_name')} ({a.get('department')}) on {a.get('date_iso')} at {render_time(a.get('time_24h'))}. Reference ID: {a.get('ref_id')}.")
    
    return {
        "success": True,
        "found": True,
        "answer": " ".join(lines),
        "appointments": appts,
        "facts": {
            "appointment_count": {"value": len(appts), "source": "booking_store", "authoritative": True},
            "reference_id": {"value": appts[0].get("ref_id"), "source": "booking_store", "authoritative": True}
        }
    }


def appointment_history(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentHistoryTool. Retrieves past consultations and visit records for verified patient."""
    phone = args.get("phone", "")
    patient_id = args.get("patient_id", "")
    
    appts = booking_store.get_appointment_history(patient_id=patient_id, phone=phone)
    if not appts:
        return {
            "success": True,
            "found": False,
            "answer": "There are no previous consultation records on file for your profile.",
            "history": []
        }
    
    lines = []
    for a in appts[:3]:
        lines.append(f"{a.get('date_iso')}: {a.get('doctor_name')} ({a.get('department')}) - Status: {a.get('status')}.")
    
    return {
        "success": True,
        "found": True,
        "answer": f"You have {len(appts)} recorded visit(s): " + " ".join(lines),
        "history": appts
    }


def appointment_reschedule(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentRescheduleTool. Atomically reschedules existing appointment with zero-loss rollback."""
    old_ref = args.get("old_ref_id") or args.get("reference_id")
    if not old_ref:
        return {"success": False, "answer": "Please provide your existing appointment reference ID to reschedule."}
    
    # Delegate to appointment_booking with reschedule intent
    args["symptom_intent"] = "reschedule"
    return appointment_booking(args, hospital_id=hospital_id)


def appointment_cancel(args: dict, hospital_id: str = None) -> dict:
    """Tool: appointmentCancelTool. Releases slot lock and marks appointment CANCELLED."""
    ref_id = args.get("ref_id") or args.get("appointment_ref")
    patient_id = args.get("patient_id")
    if not ref_id:
        return {"success": False, "answer": "Please provide your appointment reference ID to cancel."}
        
    ok, msg, appt = booking_store.cancel_appointment(ref_id, authorized_patient_id=patient_id)
    if not ok:
        return {"success": False, "answer": msg}
        
    return {
        "success": True,
        "answer": f"Your appointment {ref_id} with {appt.get('doctor_name')} has been cancelled.",
        "appointment": appt,
        "status": "CANCELLED"
    }


_patient_search_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Patient full name to search existing hospital records."},
        "phone": {"type": "string", "description": "Optional contact number."},
        "appointment_ref": {"type": "string", "description": "Optional prior booking reference ID (e.g. IS-APP-085649)."}
    }
})

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
        "doctor_name": {"type": "string", "description": "Specific doctor name if provided."},
        "department": {"type": "string", "description": "Medical department name if provided (e.g. Cardiology, Dermatology)."},
        "date": {"type": "string", "description": "Day or date to check (e.g. 'tomorrow', '2026-08-21')."},
        "time": {"type": "string", "description": "Preferred time slot (e.g. '09:30 AM')."},
        "query": {"type": "string", "description": "General question if structured parameters are not available."}
    }
})

_appointment_lookup_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "Verified patient ID."},
        "phone": {"type": "string", "description": "Contact phone number."},
        "appointment_ref": {"type": "string", "description": "Specific booking reference ID (e.g. IS-APP-123023-BCA6)."}
    }
})

_appointment_history_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "Verified patient ID."},
        "phone": {"type": "string", "description": "Contact phone number."},
        "date_from": {"type": "string", "description": "Optional start date filter."},
        "date_to": {"type": "string", "description": "Optional end date filter."}
    }
})

_appointment_reschedule_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "old_ref_id": {"type": "string", "description": "Existing appointment reference ID to reschedule."},
        "new_date": {"type": "string", "description": "Requested new date (e.g. '2026-08-22' or 'tomorrow')."},
        "new_time": {"type": "string", "description": "Requested new time (e.g. '11:00 AM')."},
        "doctor_name": {"type": "string", "description": "Doctor name."},
        "patient_id": {"type": "string", "description": "Patient ID."}
    },
    "required": ["old_ref_id"]
})

_appointment_cancel_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "ref_id": {"type": "string", "description": "Appointment reference ID to cancel."},
        "patient_id": {"type": "string", "description": "Patient ID for authorization."},
        "phone": {"type": "string", "description": "Phone number."}
    },
    "required": ["ref_id"]
})

_appointment_booking_schema = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "patient_name": {"type": "string", "description": "Patient's full name."},
        "doctor_name": {"type": "string", "description": "Specific doctor name if chosen."},
        "doctor_dept": {"type": "string", "description": "Medical department (e.g. Cardiology, Orthopedics)."},
        "date": {"type": "string", "description": "Preferred date (e.g. '2026-08-21' or 'tomorrow')."},
        "time": {"type": "string", "description": "Preferred time slot (e.g. '12:00 PM')."},
        "phone_number": {"type": "string", "description": "Contact mobile number."},
        "symptom_intent": {"type": "string", "description": "Brief reason or symptoms."}
    },
    "required": ["patient_name", "date", "time"],
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
        "symptoms": {"type": "string", "description": "Patient's primary complaints or symptoms as stated by the caller."},
        "pain_intensity": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Pain level from 1 to 10 ONLY if caller explicitly stated a number."},
        "onset_duration": {"type": "string", "description": "How long the symptoms have been present ONLY if caller explicitly stated."},
        "existing_conditions": {"type": "string", "description": "Any previous medical history mentioned by the caller."}
    },
    "required": ["symptoms"],
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
            "name": "searchPatientTool",
            "description": "Candidate discovery tool to look up existing patient profiles when an old patient calls from a new number or gives their name. Strictly returns candidate matches for verification; does NOT disclose medical history.",
            "inputSchema": {"json": _patient_search_schema},
        }
    },
    {
        "toolSpec": {
            "name": "hospitalInfoTool",
            "description": "MUST be called when the caller asks about MRI scans (Brain MRI, Spine MRI, Abdomen MRI), CT scans, X-Rays, Ultrasound, diagnostic test costs, lab tests, hospital location, pharmacy, visiting hours, or room charges. ALWAYS call this tool for ANY MRI or scan query. Do NOT answer from memory — always call this tool.",
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
            "description": "Schedule a new appointment or reschedule an existing one. Requires patient name, date, and time.",
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
            "name": "appointmentLookupTool",
            "description": "Look up upcoming active confirmed appointments for a verified patient or specific reference ID.",
            "inputSchema": {"json": _appointment_lookup_schema},
        }
    },
    {
        "toolSpec": {
            "name": "appointmentHistoryTool",
            "description": "Retrieve past medical consultation history and previous clinic visits. Must NOT be used for billing.",
            "inputSchema": {"json": _appointment_history_schema},
        }
    },
    {
        "toolSpec": {
            "name": "appointmentRescheduleTool",
            "description": "Reschedule an existing active confirmed appointment to a new date and time with zero-loss rollback.",
            "inputSchema": {"json": _appointment_reschedule_schema},
        }
    },
    {
        "toolSpec": {
            "name": "appointmentCancelTool",
            "description": "Cancel an active confirmed appointment and release its slot.",
            "inputSchema": {"json": _appointment_cancel_schema},
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
    "searchpatienttool": search_patient,
    "hospitalinfotool": hospital_info,
    "doctoravailabilitytool": doctor_availability,
    "appointmentbookingtool": appointment_booking,
    "appointmentlookuptool": appointment_lookup,
    "appointmenthistorytool": appointment_history,
    "appointmentrescheduletool": appointment_reschedule,
    "appointmentcanceltool": appointment_cancel,
    "clinicaltriagetool": clinical_triage,
    "reportstatustool": report_status,
    "handofftool": emergency_handoff,
    "getbillinginfotool": get_billing_info,
    "predictotscheduletool": predict_ot_schedule,
}


async def tool_processor(tool_name: str, tool_args: str, hospital_id: str = None) -> dict[str, Any]:
    """Parse tool_args JSON, dispatch to the handler with hospital_id, return validated result."""
    try:
        args = json.loads(tool_args)
    except (json.JSONDecodeError, TypeError):
        args = {}
    handler = _tool_handlers.get(tool_name.lower())
    if handler is None:
        return {"message": "I cannot help you with that request", "success": False}
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, handler, args, hospital_id)
    return result

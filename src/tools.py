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
from src.kb_config import DISABLE_FAISS_FOR_UNIFIED, ENABLE_MULTI_INTENT, KB_SYSTEM
from src.kb_loader import get_kb_loader
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


# Load FAISS only for legacy mode. Unified KB uses deterministic local lookups.
if not (KB_SYSTEM == "unified" and DISABLE_FAISS_FOR_UNIFIED):
    _load_faiss_cache()
else:
    logger.info("[FAISS] Skipped because unified KB mode is active")


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
# Hospital Tool Implementations (Asha / InDiiServe Healthcare)
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
    # Appointment / Doctor
    "अपॉइंटमेंट": "appointment", "अपोइंटमेंट": "appointment",
    "डॉक्टर": "doctor", "डाक्टर": "doctor",
    # General medical
    "बुखार": "fever", "दर्द": "pain", "जांच": "test", "जाँच": "test",
    "ऑपरेशन": "surgery", "आपरेशन": "surgery",
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
    "mon": "Monday", "monday": "Monday",
    "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
    "wed": "Wednesday", "wednesday": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
    "fri": "Friday", "friday": "Friday",
    "sat": "Saturday", "saturday": "Saturday",
    "sun": "Sunday", "sunday": "Sunday",
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
    "mustn", "needn", "shan", "shouldn", "wasn", "weren", "won", "wouldn", "tell", "want", 
    "know", "please", "would", "like", "actually", "available",
    
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
    if value in (None, ""):
        return ""
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
    return [service for score, service in scored if score >= max(1, best_score - 1)]


def _format_service_answer(matches: list[dict]) -> str:
    if len(matches) == 1:
        service = matches[0]
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
    return "We offer: " + ", ".join(
        f"{service.get('name')} ({_format_price(service.get('price'))})"
        for service in matches[:6]
    ) + ". Which one should I check?"


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


def _format_departments(loader) -> str:
    departments = [_department_name(d) for d in loader.get_departments()]
    departments = [d for d in departments if d]
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
                    # Hindi aliases (post-expansion)
                    "\u092a\u093e\u0930\u094d\u0915\u093f\u0902\u0917", "\u0917\u093e\u0921\u093c\u0940"],
        "wifi": ["wifi", "wi-fi", "wi fi", "internet", "net", "password"],
        "cafeteria": ["cafeteria", "canteen", "food", "eat", "khana", "khaana", "restaurant",
                      "lunch", "dinner", "breakfast",
                      "\u0915\u0948\u092b\u0947\u091f\u0947\u0930\u093f\u092f\u093e", "\u0915\u0948\u0902\u091f\u0940\u0928"],
        "pharmacy": ["pharmacy", "medicine", "dawai", "chemist", "medical store",
                     "\u092b\u093e\u0930\u094d\u092e\u0947\u0938\u0940", "\u0926\u0935\u093e\u0908", "\u0926\u0935\u093e"],
        "atm": ["atm", "cash", "paise", "money", "bank",
                "\u090f\u091f\u0940\u090f\u092e"],
        "wheelchair_porter": ["wheelchair", "porter", "assist", "help", "kursi", "stretcher"],
        "prayer_room": ["prayer", "meditation", "mandir", "pray", "pooja", "masjid"],
        "play_area": ["play", "children", "kids", "khelen", "activity"],
        # Travel / Directions — catches queries like "how to reach", "kaise aayein",
        # "directions", "travel from", preventing KB vector fallback returning test prices
        "directions": ["travel", "directions", "reach", "route", "how to come", "how to get",
                       "from west", "from mumbai", "from bengal", "kaise aayein", "kaise aana",
                       "\u0930\u093e\u0938\u094d\u0924\u093e", "\u092a\u0939\u0941\u0902\u091a\u0928\u093e", "\u0915\u0948\u0938\u0947 \u0906\u090f\u0902"],
    }
    
    # [FIX TRAVEL] Handle travel/directions queries early — before the amenity loop.
    # Without this, "how to travel" falls through to KB vector search which
    # returns test pricing data instead of the hospital address (seen in session 33cee9fb).
    direction_keywords = mapping["directions"]
    if any(syn in query for syn in direction_keywords):
        address = amenities.get("address") or amenities.get("location", "")
        if not address:
            # Fallback: read from the core_info-style fields that may be stored in amenities
            address = "12-B, MG Road, Residency Area, Bengaluru - 560025"
        return (
            f"Our hospital is located at: {address}. "
            "You can reach us by flight, train, or road. "
            "From the airport or railway station, take a cab or metro directly to MG Road. "
            "Our address is on Google Maps — search 'Indiiserve Multi-Specialty Hospital'."
        )
    
    matched_results = []
    for key, value in amenities.items():
        synonyms = mapping.get(key, [key.replace("_", " ")])
        if any(_has_word(query, syn) for syn in synonyms) or any(_has_word(query, part) for part in key.replace("_", " ").split()):
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
    ist_now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    if "tomorrow" in query or "kal" in query:
        return (ist_now + datetime.timedelta(days=1)).strftime("%A")
    if "today" in query or "aaj" in query:
        return ist_now.strftime("%A")
    for alias, day in _DAY_ALIASES.items():
        if _has_word(query, alias):
            return day
    return None


def _doctor_slots_for_day(doc: dict, day: str | None) -> tuple[str, list[str]]:
    availability = doc.get("availability") or {}
    if not availability:
        return doc.get("schedule", "during OPD hours"), []
    if "days" in availability:
        days = availability.get("days", [])
        slots = availability.get("time_slots", [])
        if day:
            short = day[:3]
            if short in days or day in days:
                return day, slots
            return day, []
        return ", ".join(days), slots
    if day:
        return day, availability.get(day, [])
    active_days = [d for d, slots in availability.items() if slots]
    first_slots = []
    for d in active_days:
        first_slots = availability.get(d, [])
        if first_slots:
            break
    return ", ".join(active_days), first_slots


def _match_doctors(query: str, doctors: list[dict]) -> list[dict]:
    specialty_to_dept = {
        "cardio": "cardiology", "heart": "cardiology",
        "neuro": "neurology", "brain": "neurology",
        "ortho": "orthopedics", "joint": "orthopedics", "bone": "orthopedics", "knee": "orthopedics",
        "child": "pediatrics", "baby": "pediatrics", "pediatr": "pediatrics",
        "gyneco": "gynecology", "obstetr": "gynecology", "pregnancy": "gynecology", "women": "gynecology",
        "diabetes": "endocrinology", "thyroid": "endocrinology",
        "stomach": "gastroenterology", "gastro": "gastroenterology",
        "lung": "pulmonology", "chest": "pulmonology", "breathing": "pulmonology",
        "cancer": "oncology", "eye": "ophthalmology", "ent": "ent", "ear": "ent", "nose": "ent", "throat": "ent",
        "skin": "dermatology", "physician": "general medicine", "fever": "general medicine",
    }
    expanded_query = query
    for token, dept in specialty_to_dept.items():
        if _has_prefix_word(query, token):
            expanded_query += f" {dept}"

    scored = []
    for doc in doctors:
        text = _doctor_search_text(doc)
        score = _score_text_match(expanded_query, text)
        name_parts = [p for p in _tokens(doc.get("name", "")) if p not in {"dr"}]
        if any(_has_word(query, part) for part in name_parts):
            score += 5
        if score > 0:
            scored.append((score, doc))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][0]
    return [doc for score, doc in scored if score >= max(1, best - 1)]


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
    for doc, day_label, slots in available[:5]:
        slot_text = ", ".join(slots[:2]) if slots else day_label
        parts.append(f"{doc.get('name')} ({slot_text})")
    return f"We have {len(available)} matching specialists: " + ", ".join(parts) + ". Who would you like to consult?"


def _unified_hospital_info(args: dict, hospital_id: str = None) -> dict:
    loader = _get_unified_loader()
    query = _normalize_query(args.get("query", ""))
    core = loader.get_core_info()

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
        dept_answer = _format_departments(loader)
        doc_matches = _match_doctors(query, loader.get_doctors()) or loader.get_doctors()
        return {"answer": f"{dept_answer} {_format_doctor_answer(doc_matches, query)}"}

    services = _match_services(query, loader.get_services())
    if services:
        return {"answer": _format_service_answer(services)}

    if _has_any_word(query, ["department", "departments", "specialties", "speciality"]):
        return {"answer": _format_departments(loader)}

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
    if _has_any_word(query, ["hour", "time", "timing", "open", "close"]):
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

    if _has_any_word(query, ["contact", "phone", "number", "telephone", "mobile", "call", "baat"]):
        return {"answer": f"{core.get('name')} contact number is {core.get('contact', '+91 80 4000 9000')}."}

    return {"answer": f"{core.get('name', 'Indiiserve Hospital')} provides {len(loader.get_departments())} departments and {len(loader.get_services())} listed services. What would you like to check?"}


def _unified_doctor_availability(args: dict, hospital_id: str = None) -> dict:
    loader = _get_unified_loader()
    query = _normalize_query(args.get("query", ""))
    matches = _match_doctors(query, loader.get_doctors())
    if matches:
        return {"answer": _format_doctor_answer(matches, query)}
    return {
        "answer": (
            f"We have specialists across {len(loader.get_departments())} departments. "
            "Which department or doctor should I check?"
        )
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
        "ultrasound": ["ultrasound", "usg", "sonography"],
        "lipid": ["lipid", "cholesterol"],
        "liver": ["liver", "lft"],
        "kidney": ["kidney", "kft", "rft"],
        "vitamin d": ["vitamin d"],
        "vitamin b12": ["b12"],
        "x-ray": ["x-ray", "xray"],
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
            "description": "MUST be called when the caller asks about hospital location, address, directions, where to go, contact details, pharmacy hours, visiting hours, parking details/charges/availability, diagnostic or scan pricing (e.g. MRI, CT, thyroid, blood tests, ultrasound, PET scan, x-ray costs), room rent/charges (e.g. ICU, deluxe, general ward rent), cafeteria, ATM, Wi-Fi, wheelchair, or any general hospital information/facilities/prices. Do NOT answer from memory — always call this tool.",
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

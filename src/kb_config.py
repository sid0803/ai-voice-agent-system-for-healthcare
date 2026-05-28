# Feature Flags & Configuration for Knowledge Base System
# Enable/disable new unified KB system for safe deployment

import os
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# FEATURE FLAG: Use Unified KB System
# ============================================================================
# Set to "unified" to use unified_hospital_kb.json.
# Legacy hospital_data/distilled_facts files have been removed.
# Default: "unified" (single source of truth for production)
# 
# Migration Path:
# Deploy with KB_SYSTEM="unified" (single source of truth).
KB_SYSTEM = os.getenv("KB_SYSTEM", "unified")

# Allowed values
ALLOWED_KB_SYSTEMS = ["unified"]
if KB_SYSTEM not in ALLOWED_KB_SYSTEMS:
    logger.warning(f"[CONFIG] Invalid KB_SYSTEM={KB_SYSTEM}. Using 'legacy' as fallback.")
    KB_SYSTEM = "legacy"

# ============================================================================
# Feature Flags for Optimization
# ============================================================================

# Disable FAISS cache when using unified KB (faster, no redundancy)
DISABLE_FAISS_FOR_UNIFIED = os.getenv("DISABLE_FAISS_FOR_UNIFIED", "true").lower() == "true"

# Enable multi-intent resolver for compound receptionist queries.
ENABLE_MULTI_INTENT = os.getenv("ENABLE_MULTI_INTENT", "true").lower() == "true"

# Enable context awareness (requires AgentCore Memory or session tracking)
ENABLE_CONTEXT_AWARENESS = os.getenv("ENABLE_CONTEXT_AWARENESS", "false").lower() == "true"

# Target response latency optimization (ms)
TARGET_RESPONSE_LATENCY_MS = int(os.getenv("TARGET_RESPONSE_LATENCY_MS", "600"))

# ============================================================================
# Logging Configuration
# ============================================================================
ENABLE_KB_DEBUG_LOGGING = os.getenv("ENABLE_KB_DEBUG_LOGGING", "false").lower() == "true"

def log_config():
    """Log current configuration for debugging"""
    logger.info("[CONFIG] Knowledge Base System Configuration:")
    logger.info(f"  KB_SYSTEM: {KB_SYSTEM}")
    logger.info(f"  DISABLE_FAISS_FOR_UNIFIED: {DISABLE_FAISS_FOR_UNIFIED}")
    logger.info(f"  ENABLE_MULTI_INTENT: {ENABLE_MULTI_INTENT}")
    logger.info(f"  ENABLE_CONTEXT_AWARENESS: {ENABLE_CONTEXT_AWARENESS}")
    logger.info(f"  TARGET_RESPONSE_LATENCY_MS: {TARGET_RESPONSE_LATENCY_MS}")

# Call on module import
log_config()

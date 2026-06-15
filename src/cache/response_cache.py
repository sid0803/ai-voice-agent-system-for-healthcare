"""PCM Response Cache for cost optimization.

Stores and retrieves pre-rendered audio chunks for common intents 
(Greetings, Transfers, etc.) to minimize Bedrock Nova Sonic 
invocation costs and reduce time-to-first-byte (TTFB).
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class ResponseCache:
    """Manages static PCM audio assets for the voice agent."""

    def __init__(self, assets_dir: str):
        self.assets_dir = Path(assets_dir)
        self._cache = {}
        self._load_warm_assets()

    def _load_warm_assets(self):
        """Loads critical assets into memory on startup."""
        critical_assets = ["hello.pcm", "transfer.pcm", "emergency.pcm", "greeting.pcm"]
        for asset in critical_assets:
            path = self.assets_dir / asset
            if path.exists():
                try:
                    self._cache[asset] = path.read_bytes()
                    logger.info(f"Warmed cache with asset: {asset}")
                except Exception as e:
                    logger.error(f"Failed to load asset {asset}: {e}")

    def get_audio(self, asset_id: str) -> bytes:
        """Retrieve PCM bytes for a given asset ID."""
        # Normalize asset_id to filename if needed
        filename = asset_id if asset_id.endswith(".pcm") else f"{asset_id}.pcm"
        
        # Check memory first
        if filename in self._cache:
            return self._cache[filename]
        
        # Fallback to disk
        path = self.assets_dir / filename
        if path.exists():
            data = path.read_bytes()
            self._cache[filename] = data # Lazily cache
            return data
            
        logger.warning(f"Audio asset {asset_id} not found in cache or disk.")
        return b""

# Global singleton initialized with assets directory relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
response_cache = ResponseCache(str(_PROJECT_ROOT / "assets"))

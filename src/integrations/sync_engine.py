import asyncio
import json
import logging
import socket
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timezone
import httpx
from src.analytics.rds_client import rds_analytics
from src.integrations.adapter import data_adapter

logger = logging.getLogger(__name__)

class SyncEngine:
    """The Heart of Ingestion: Handles Real-time Pushes and Scheduled Pulls."""

    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.semaphore = asyncio.Semaphore(5) # Limit concurrency (Requirement: Reliability)

    def is_safe_url(self, url: str) -> bool:
        """Requirement Hardening: SSRF Protection. Blocks non-public IP ranges."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ["http", "https"]:
                return False
            
            hostname = parsed.hostname
            if not hostname:
                return False

            # Resolve hostname to IP
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)

            # Block loopback, private, link-local, and multicast addresses
            if (ip_obj.is_loopback or 
                ip_obj.is_private or 
                ip_obj.is_link_local or 
                ip_obj.is_multicast or
                ip_obj.is_reserved):
                logger.warning(f"[SECURITY] Blocked SSRF attempt to internal IP: {ip} for URL {url}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"[SECURITY] URL validation error for {url}: {e}")
            return False

    async def process_push(self, hospital_id: str, raw_data: dict, push_token: str) -> bool:
        """Requirement Hardening: Authenticated API Push."""
        conn = rds_analytics.get_connection()
        if not conn:
            return False
            
        try:
            with conn.cursor() as cur:
                # 1. Verify Token
                cur.execute("SELECT push_token FROM tenants WHERE hospital_id = %s", (hospital_id,))
                res = cur.fetchone()
                if not res or res[0] != push_token:
                    logger.warning(f"[SYNC] Unauthorized push attempt for {hospital_id}")
                    return False

                # 2. Normalize and Save
                normalized = data_adapter.normalize(raw_data)
                cur.execute("""
                    UPDATE tenants 
                    SET hospital_data_normalized = %s, last_sync_at = NOW() 
                    WHERE hospital_id = %s
                """, (json.dumps(normalized), hospital_id))
                conn.commit()
                logger.info(f"[SYNC] Real-time push successful for {hospital_id}")
                return True
        except Exception:
            logger.exception(f"[SYNC] Push processing failed for {hospital_id}")
            return False
        finally:
            conn.close()

    async def scheduled_pull_worker(self):
        """Background Thread: The 10-minute fallback sync."""
        logger.info("[SYNC] Background sync worker started (Adaptive Frequency).")
        while True:
            try:
                await self._perform_all_syncs()
            except Exception:
                logger.exception("[SYNC] Background worker iteration failed")
            
            # Default sleep 5 minutes between checks
            await asyncio.sleep(300)

    async def _perform_all_syncs(self):
        """Iterate over tenants and pull data if required."""
        conn = rds_analytics.get_connection()
        if not conn:
            return
            
        try:
            with conn.cursor() as cur:
                # Select only 'live' or 'sandbox' tenants set for hybrid/pull
                cur.execute("""
                    SELECT hospital_id, ingestion_config, sync_interval_mins, last_sync_at 
                    FROM tenants 
                    WHERE status IN ('live', 'sandbox') 
                    AND ingestion_strategy IN ('pull', 'hybrid')
                """)
                tenants = cur.fetchall()
                
            # Implementation Hardening: Parallel Sync (P1)
            # We use a semaphore to avoid overloading the DB or CPU
            tasks = []
            for tid, config_raw, interval, last_sync in tenants:
                if not config_raw or not config_raw.get("pull_url"):
                    continue
                    
                # Check if it's time to sync
                now = datetime.now(timezone.utc)
                if last_sync:
                    seconds_since = (now - last_sync.replace(tzinfo=timezone.utc)).total_seconds()
                    if seconds_since < (interval * 60):
                        continue
                
                # Queue the task
                tasks.append(self._sync_with_semaphore(tid, config_raw))
            
            if tasks:
                await asyncio.gather(*tasks)
                
        finally:
            conn.close()

    async def _sync_with_semaphore(self, hospital_id: str, config: dict):
        """Wrapper to respect concurrency limits."""
        async with self.semaphore:
            await self._sync_single_tenant(hospital_id, config)

    async def _sync_single_tenant(self, hospital_id: str, config: dict):
        """Pull data from external HIS API."""
        url = config.get("pull_url")
        headers = config.get("headers", {})
        
        # SSRF Protection (P0 Hardening)
        if not self.is_safe_url(url):
            logger.error(f"[SECURITY] Sync skipped for {hospital_id} due to unsafe URL: {url}")
            return

        try:
            resp = await self.http_client.get(url, headers=headers)
            resp.raise_for_status()
            raw_data = resp.json()
            
            # Normalize and update DB
            normalized = data_adapter.normalize(raw_data)
            
            conn = rds_analytics.get_connection()
            if conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE tenants 
                        SET hospital_data_normalized = %s, last_sync_at = NOW() 
                        WHERE hospital_id = %s
                    """, (json.dumps(normalized), hospital_id))
                    conn.commit()
                    conn.close()
                logger.info(f"[SYNC] Scheduled pull successful for {hospital_id}")
        except Exception as e:
            logger.error(f"[SYNC] Failed to pull data for {hospital_id}: {str(e)}")

# Global instance
sync_engine = SyncEngine()

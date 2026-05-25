import asyncio
import json
import logging
import socket
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timezone
import httpx
from src.analytics.dynamodb_client import dynamodb_analytics
from src.integrations.adapter import data_adapter
from src.learning.distiller import learning_distiller
from src.tools import sync_community_knowledge

logger = logging.getLogger(__name__)

class SyncEngine:
    """The Heart of Ingestion: Handles Real-time Pushes and Scheduled Pulls."""

    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.semaphore = asyncio.Semaphore(5) # Limit concurrency (Requirement: Reliability)

    async def is_safe_url(self, url: str) -> bool:
        """Requirement Hardening: SSRF Protection. Blocks non-public IP ranges."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ["http", "https"]:
                return False
            
            hostname = parsed.hostname
            if not hostname:
                return False

            # [MED FIX] Use asyncio.to_thread for blocking DNS resolution
            ip = await asyncio.to_thread(getattr(socket, "gethostbyname"), hostname)
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
        try:
            # 1. Fetch Tenant
            tenant = await asyncio.to_thread(dynamodb_analytics.get_tenant, hospital_id)
            if not tenant:
                logger.warning(f"[SYNC] Hospital ID {hospital_id} not found.")
                return False
            
            stored_token = tenant.get("push_token")
            if not stored_token or stored_token != push_token:
                logger.warning(f"[SYNC] Unauthorized push attempt for {hospital_id}")
                return False

            # 2. Normalize and Save
            normalized = data_adapter.normalize(raw_data)
            tenant["hospital_data_normalized"] = normalized
            tenant["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            
            saved = await asyncio.to_thread(dynamodb_analytics.save_tenant, tenant)
            if saved:
                logger.info(f"[SYNC] Real-time push successful for {hospital_id}")
                return True
            return False
        except Exception:
            logger.exception(f"[SYNC] Push processing failed for {hospital_id}")
            return False

    async def scheduled_pull_worker(self):
        """Background Thread: The 10-minute fallback sync."""
        logger.info("[SYNC] Background sync worker started (Adaptive Frequency).")
        while True:
            try:
                await self._perform_all_syncs()
            except Exception:
                logger.exception("[SYNC] Background worker iteration failed")
            
            # 2. Run Automatic Learning (Knowledge Distillation)
            try:
                if learning_distiller.run_learning_cycle():
                    sync_community_knowledge()
            except Exception:
                logger.exception("[LEARNING] Automatic distillation cycle failed")

            # Default sleep 5 minutes between checks
            await asyncio.sleep(300)

    async def _perform_all_syncs(self):
        """Iterate over tenants and pull data if required."""
        try:
            # Select only 'live' or 'sandbox' tenants set for hybrid/pull
            tenants = await asyncio.to_thread(dynamodb_analytics.list_tenants)
            
            # Implementation Hardening: Parallel Sync (P1)
            # We use a semaphore to avoid overloading the DB or CPU
            tasks = []
            for tenant in tenants:
                status = tenant.get("status")
                strategy = tenant.get("ingestion_strategy")
                if status not in ['live', 'sandbox'] or strategy not in ['pull', 'hybrid']:
                    continue
                
                config_raw = tenant.get("ingestion_config")
                if not config_raw or not config_raw.get("pull_url"):
                    continue
                
                interval = tenant.get("sync_interval_mins", 10)
                last_sync = tenant.get("last_sync_at")
                
                # Check if it's time to sync
                now = datetime.now(timezone.utc)
                if last_sync:
                    try:
                        last_sync_dt = datetime.fromisoformat(last_sync)
                        seconds_since = (now - last_sync_dt).total_seconds()
                        if seconds_since < (interval * 60):
                            continue
                    except Exception:
                        pass
                
                # Queue the task
                tasks.append(self._sync_with_semaphore(tenant["hospital_id"], config_raw))
            
            if tasks:
                await asyncio.gather(*tasks)
        except Exception:
            logger.exception("[SYNC] Failed to perform scheduled syncs")

    async def _sync_with_semaphore(self, hospital_id: str, config: dict):
        """Wrapper to respect concurrency limits."""
        async with self.semaphore:
            await self._sync_single_tenant(hospital_id, config)

    async def _sync_single_tenant(self, hospital_id: str, config: dict):
        """Pull data from external HIS API with DNS-pinned connection (CRIT-04)."""
        url = config.get("pull_url")
        headers = config.get("headers", {})
        
        # SSRF Protection (P0 Hardening)
        if not await self.is_safe_url(url):
            logger.error(f"[SECURITY] Sync skipped for {hospital_id} due to unsafe URL: {url}")
            return

        try:
            # [CRIT-04] DNS Pinning: resolve once and inject resolved IP as the target
            # [MED FIX] Use asyncio.to_thread for blocking DNS resolution
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(url)
            resolved_ip = await asyncio.to_thread(getattr(socket, "gethostbyname"), parsed.hostname)
            
            # Re-validate the resolved IP (redundant safety check)
            ip_obj = ipaddress.ip_address(resolved_ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                logger.error(f"[SECURITY] IP re-validation failed for {hospital_id}: {resolved_ip}")
                return

            # Build pinned URL: replace hostname with resolved IP, preserve Host header
            pinned = parsed._replace(netloc=f"{resolved_ip}:{parsed.port}" if parsed.port else resolved_ip)
            pinned_url = urlunparse(pinned)
            pinned_headers = {**headers, "Host": parsed.hostname}  # Keep original Host header

            resp = await self.http_client.get(pinned_url, headers=pinned_headers)
            resp.raise_for_status()
            raw_data = resp.json()
            
            # Normalize and update DB
            normalized = data_adapter.normalize(raw_data)
            
            tenant = await asyncio.to_thread(dynamodb_analytics.get_tenant, hospital_id)
            if tenant:
                tenant["hospital_data_normalized"] = normalized
                tenant["last_sync_at"] = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(dynamodb_analytics.save_tenant, tenant)
                logger.info(f"[SYNC] Scheduled pull successful for {hospital_id}")
        except Exception as e:
            logger.error(f"[SYNC] Failed to pull data for {hospital_id}: {str(e)}")

# Global instance
sync_engine = SyncEngine()

"""Server-Sent Events (SSE) live push stream for admin dashboard."""

import json
import asyncio
import logging
from typing import Dict, Any

from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from src.admin.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["Live Server-Sent Events"])


@router.get("/stream")
async def event_stream(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    """Tenant-scoped SSE stream with periodic heartbeats for real-time dashboard updates."""
    tenant_id = user["tenant_id"]

    async def event_generator():
        logger.info("[SSE] Client connected for user %s on tenant %s", user["user_id"], tenant_id)
        try:
            # 1. Send initial connection confirmation event
            init_data = json.dumps({
                "event": "connected",
                "tenant_id": tenant_id,
                "user": user["user_id"],
                "timestamp": asyncio.get_running_loop().time(),
            })
            yield f"event: connect\ndata: {init_data}\n\n"

            # 2. Periodic heartbeat & event loop
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("[SSE] Client disconnected: %s", user["user_id"])
                    break

                # Send 25-second heartbeat ping
                await asyncio.sleep(25)
                yield f":ping\n\n"

        except asyncio.CancelledError:
            logger.info("[SSE] Stream cancelled for %s", user["user_id"])
        except Exception as e:
            logger.warning("[SSE] Error in stream generator: %s", e)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

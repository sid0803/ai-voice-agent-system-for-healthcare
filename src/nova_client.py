"""Nova Sonic bidirectional stream client and session management.

Python equivalent of TypeScript's nova-client.ts.
Provides S2SBidirectionalStreamClient for managing bidirectional streaming
sessions with Amazon Bedrock's Nova Sonic model, and StreamSession as a
high-level wrapper for audio buffering and session setup.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

import requests
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamOperationInput,
    InvokeModelWithBidirectionalStreamInputChunk,
    BidirectionalInputPayloadPart,
)
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from smithy_aws_core.identity import EnvironmentCredentialsResolver


def get_imdsv2_token(timeout: int = 2) -> str:
    """Get IMDSv2 token for EC2 metadata access."""
    r = requests.put(
        "http://169.254.169.254/latest/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.text


def get_ec2_iam_role_credentials(timeout: int = 2) -> dict:
    """Load IAM role credentials from EC2 metadata (IMDSv2)."""
    try:
        token = get_imdsv2_token(timeout=timeout)
        headers = {"X-aws-ec2-metadata-token": token}
        
        role_name = requests.get(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            headers=headers,
            timeout=timeout,
        ).text.strip()
        
        if not role_name:
            return {}
        
        creds = requests.get(
            f"http://169.254.169.254/latest/meta-data/iam/security-credentials/{role_name}",
            headers=headers,
            timeout=timeout,
        ).json()
        
        return {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["Token"]
        }
    except Exception:
        return {}

from .tools import available_tools, tool_processor
from .types_config import (
    DEFAULT_AUDIO_INPUT_CONFIG,
    DEFAULT_AUDIO_OUTPUT_CONFIG,
    DEFAULT_INFERENCE_CONFIG,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEXT_CONFIG,
    AudioConfiguration,
    InferenceConfig,
    TextConfiguration,
)

logger = logging.getLogger(__name__)
# NOTE: Do NOT call logging.basicConfig() here. server.py owns the root log config.


@dataclass
class SessionData:
    """State for a single streaming session."""

    stream: Any = None  # The bidirectional stream object
    tool_use_content: Any = None
    tool_use_id: str = ""
    tool_name: str = ""
    response_handlers: dict[str, Callable] = field(default_factory=dict)
    prompt_name: str = field(default_factory=lambda: str(uuid4()))
    inference_config: InferenceConfig = field(default_factory=InferenceConfig)
    is_active: bool = True
    is_prompt_start_sent: bool = False
    is_audio_content_start_sent: bool = False
    audio_content_id: str = field(default_factory=lambda: str(uuid4()))
    audio_paused: bool = False
    hospital_id: str = "default_tier2"
    is_audio_data_sent: bool = False
    audio_ever_sent: bool = False
    open_content_ids: set[str] = field(default_factory=set)
    # [MED-06] asyncio.Event to signal when Bedrock stream is ready
    # Server waits on this instead of polling, eliminating the busy-wait loop.
    _stream_ready: asyncio.Event = field(default_factory=asyncio.Event)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tool_call_pending: bool = False
    assistant_speaking: bool = False
    active_tool_calls: set[str] = field(default_factory=set)
    pending_tools: dict[str, dict] = field(default_factory=dict)
    current_content_id: str = ""
    completion_received: bool = False


class StreamSession:
    """High-level wrapper for a single Nova Sonic streaming session.

    Provides audio buffering, event handler registration, and delegates
    to S2SBidirectionalStreamClient for actual streaming operations.
    """

    def __init__(self, session_id: str, client: S2SBidirectionalStreamClient) -> None:
        self._session_id = session_id
        self._client = client
        self._audio_buffer_queue: list[bytes] = []
        # [OPT-09] 10 frames = 200ms max buffer (was 200 frames = 4 seconds!).
        # Audio is forwarded to Nova Sonic immediately instead of being queued,
        # reducing perceived response start latency.
        self._max_queue_size: int = 10
        self._is_processing_audio: bool = False
        self._is_active: bool = True
        self.stream_sid: str = ""
        self._hospital_id: str = "default_tier2"

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def hospital_id(self) -> str:
        return self._hospital_id

    @hospital_id.setter
    def hospital_id(self, value: str) -> None:
        self._hospital_id = value
        session_data = self._client._active_sessions.get(self._session_id)
        if session_data:
            session_data.hospital_id = value

    def on_event(self, event_type: str, handler: Callable) -> StreamSession:
        """Register an event handler for this session. Returns self for chaining."""
        self._client.register_event_handler(self._session_id, event_type, handler)
        return self

    async def setup_prompt_start(self) -> None:
        await self._client.setup_prompt_start_event(self._session_id)

    async def setup_system_prompt(self, text_config=None, system_prompt: str = None) -> None:
        await self._client.setup_system_prompt_event(
            self._session_id,
            text_config or DEFAULT_TEXT_CONFIG,
            system_prompt or DEFAULT_SYSTEM_PROMPT,
        )

    async def setup_start_audio(self, audio_config=None) -> None:
        await self._client.setup_start_audio_event(
            self._session_id,
            audio_config or DEFAULT_AUDIO_INPUT_CONFIG,
        )

    async def stream_audio(self, audio_data: bytes) -> None:
        """Buffer audio data and trigger queue processing."""
        if self._client.is_audio_paused(self._session_id):
            return  # Drop audio while paused (idle follow-up in progress)
        if len(self._audio_buffer_queue) >= self._max_queue_size:
            self._audio_buffer_queue.pop(0)  # Drop oldest
        self._audio_buffer_queue.append(audio_data)
        await self._process_audio_queue()

    async def _process_audio_queue(self) -> None:
        """Process buffered audio in batches of up to 5 chunks."""
        if self._is_processing_audio or not self._audio_buffer_queue or not self._is_active or self._client.is_audio_paused(self._session_id):
            return
        self._is_processing_audio = True
        try:
            processed_chunks = 0
            # [LOW FIX] Matched to queue size (OPT-09) to ensure consistent drain
            max_chunks_per_batch = 10
            while (
                self._audio_buffer_queue
                and processed_chunks < max_chunks_per_batch
                and self._is_active
                and not self._client.is_audio_paused(self._session_id)
            ):
                audio_chunk = self._audio_buffer_queue.pop(0)
                await self._client.stream_audio_chunk(self._session_id, audio_chunk)
                processed_chunks += 1
        finally:
            self._is_processing_audio = False
            if self._audio_buffer_queue and self._is_active and not self._client.is_audio_paused(self._session_id):
                asyncio.ensure_future(self._process_audio_queue())

    async def end_audio_content(self) -> None:
        if not self._is_active:
            return
        await self._client.send_content_end(self._session_id)

    async def end_prompt(self) -> None:
        if not self._is_active:
            return
        await self._client.send_prompt_end(self._session_id)

    async def close(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        self._audio_buffer_queue.clear()
        await self._client.close_session(self._session_id)


class S2SBidirectionalStreamClient:
    """Manages bidirectional streaming sessions with Amazon Bedrock Nova Sonic.

    Uses the smithy-based aws-sdk-bedrock-runtime for bidirectional streaming.
    """

    def __init__(
        self,
        region: str = "us-east-1",
        credentials: Optional[dict] = None,
        inference_config: Optional[InferenceConfig] = None,
        model_id: str = "amazon.nova-2-sonic-v1:0",
    ) -> None:
        # Use implicit environment resolution via smithy resolver, which works fine
        # if the variables are set via python-dotenv upstream. We won't mutate os.environ explicitly.
        
        credentials = credentials or {}
        
        # Try loading IAM role credentials from EC2 IMDS if no explicit creds or env vars
        if not os.environ.get("AWS_ACCESS_KEY_ID") and not credentials.get("aws_access_key_id"):
            ec2_creds = get_ec2_iam_role_credentials(timeout=2)
            if ec2_creds.get("aws_access_key_id"):
                os.environ["AWS_ACCESS_KEY_ID"] = ec2_creds["aws_access_key_id"]
                os.environ["AWS_SECRET_ACCESS_KEY"] = ec2_creds["aws_secret_access_key"]
                os.environ["AWS_SESSION_TOKEN"] = ec2_creds["aws_session_token"]
                logger.info("Using IAM role credentials from EC2 metadata (IMDSv2)")
            else:
                logger.info("Could not fetch IAM role creds from metadata; relying on IAM identity or defaults")

        self._region = region
        self._model_id = model_id
        resolver = EnvironmentCredentialsResolver()
        cfg = Config(
            endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
            region=region,
            aws_credentials_identity_resolver=resolver,
            auth_scheme_resolver=HTTPAuthSchemeResolver(),
            auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
        )
        self._bedrock_client = BedrockRuntimeClient(config=cfg)
        self._inference_config = inference_config or DEFAULT_INFERENCE_CONFIG
        self._active_sessions: dict[str, SessionData] = {}
        self._session_last_activity: dict[str, float] = {}
        self._session_cleanup_in_progress: set[str] = set()

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    def create_stream_session(
        self, session_id: Optional[str] = None, inference_config: Optional[InferenceConfig] = None
    ) -> StreamSession:
        if session_id is None:
            session_id = str(uuid4())
        if session_id in self._active_sessions:
            raise ValueError(f"Stream session with ID {session_id} already exists")

        session = SessionData(
            stream=None,
            tool_use_content=None,
            tool_use_id="",
            tool_name="",
            response_handlers={},
            prompt_name=str(uuid4()),
            inference_config=inference_config or self._inference_config,
            is_active=True,
            is_prompt_start_sent=False,
            is_audio_content_start_sent=False,
            audio_content_id=str(uuid4()),
            tool_call_pending=False,
            assistant_speaking=False,
            active_tool_calls=set(),
            pending_tools={},
            current_content_id="",
            completion_received=False,
            # [D-11] hospital_id injected later via session.hospital_id = ...
            # SessionData.hospital_id default is fine; StreamSession.hospital_id is the live field
        )
        self._active_sessions[session_id] = session
        self._update_session_activity(session_id)
        return StreamSession(session_id, self)

    # ------------------------------------------------------------------
    # Session query helpers
    # ------------------------------------------------------------------

    def is_session_active(self, session_id: str) -> bool:
        session = self._active_sessions.get(session_id)
        return session is not None and session.is_active

    def get_active_sessions(self) -> list[str]:
        return list(self._active_sessions.keys())

    def get_last_activity_time(self, session_id: str) -> float:
        return self._session_last_activity.get(session_id, 0)

    def _update_session_activity(self, session_id: str) -> None:
        self._session_last_activity[session_id] = time.time()

    def is_cleanup_in_progress(self, session_id: str) -> bool:
        return session_id in self._session_cleanup_in_progress

    def is_audio_paused(self, session_id: str) -> bool:
        session = self._active_sessions.get(session_id)
        return session is not None and session.audio_paused

    def is_assistant_speaking(self, session_id: str) -> bool:
        session = self._active_sessions.get(session_id)
        return session is not None and session.assistant_speaking

    # ------------------------------------------------------------------
    # Event handler registration
    # ------------------------------------------------------------------

    def register_event_handler(
        self, session_id: str, event_type: str, handler: Callable
    ) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        session.response_handlers[event_type] = handler

    # ------------------------------------------------------------------
    # Send event to Bedrock stream
    # ------------------------------------------------------------------

    async def _send_event(self, session_id: str, payload: dict) -> None:
        """Send a JSON event to the Bedrock bidirectional stream."""
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active or session.stream is None:
            return
        self._update_session_activity(session_id)
        event_json = json.dumps(payload)
        # Support Mock Engine interface
        if hasattr(session.stream, "send_event"):
            await session.stream.send_event(event_json)
            return

        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(
                bytes_=event_json.encode("utf-8")
            )
        )
        await session.stream.input_stream.send(chunk)

    def _dispatch_event(self, session_id: str, event_type: str, data: Any = None) -> None:
        """Dispatch an event to the registered handler for the session."""
        session = self._active_sessions.get(session_id)
        if session is None:
            return
        handler = session.response_handlers.get(event_type)
        if handler is not None:
            try:
                handler(data)
            except Exception:
                logger.exception("Error in event handler for %s", event_type)
        any_handler = session.response_handlers.get("any")
        if any_handler is not None:
            try:
                any_handler({"type": event_type, "data": data})
            except Exception:
                logger.exception("Error in 'any' event handler for %s", event_type)

    # ------------------------------------------------------------------
    # Session initiation
    # ------------------------------------------------------------------

    async def initiate_session(self, session_id: str) -> None:
        """Initiate the bidirectional stream for a session."""
        session = self._active_sessions.get(session_id)
        if session is None:
            raise ValueError(f"Stream session {session_id} not found")

        # --- FORCE MOCK MODE IF DUMMY CREDENTIALS (P0 Hardening for Demos) ---
        aws_id = str(os.environ.get("AWS_ACCESS_KEY_ID", ""))
        has_creds = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not has_creds or "your_access_key" in aws_id or "mock_" in aws_id:
            logger.warning("[BEDROCK] Missing or dummy credentials. Forcing Clinical Mock Mode.")
            await self._activate_mock_mode(session_id, "Incomplete Environment Configuration")
            return

        try:
            stream = await asyncio.wait_for(
                self._bedrock_client.invoke_model_with_bidirectional_stream(
                    InvokeModelWithBidirectionalStreamOperationInput(
                        model_id=self._model_id
                    )
                ),
                timeout=12.0, # Faster timeout to trigger Mock Mode
            )
            session.stream = stream
            session.is_active = True

            # Send sessionStart event FIRST
            await self._send_event(session_id, {
                "event": {
                    "sessionStart": {
                        "inferenceConfiguration": {
                            "maxTokens": session.inference_config.max_tokens,
                            "topP": session.inference_config.top_p,
                            "temperature": session.inference_config.temperature,
                        },
                        "turnDetectionConfiguration": {
                            "endpointingSensitivity": "HIGH",
                        },
                    }
                }
            })

            # [MED-06] Signal stream is ready AFTER sessionStart is sent
            session._stream_ready.set()

            # Start processing response stream (blocks until error or completion)
            await self._process_response_stream(session_id)
        except Exception as exc:
            await self._activate_mock_mode(session_id, str(exc))

    async def _activate_mock_mode(self, session_id: str, reason: str) -> None:
        """Activate the Clinical Mock Engine for a session."""
        session = self._active_sessions.get(session_id)
        if not session:
            return

        logger.warning(f"[BEDROCK] Switching to Clinical Mock Mode: {reason}")
        
        # --- START CLINICAL MOCK MODE (Requirement: Offline Demo Stability) ---
        from .mock_engine import MockS2SStream
        session.stream = MockS2SStream(session_id, self)
        await session.stream.start_processing()
        
        # Ensure session is active before notify/start
        session.is_active = True
        
        # [D-07] Signal stream ready so server.py doesn't wait 30s and time out.
        # Mock mode IS ready immediately after MockS2SStream.start_processing().
        session._stream_ready.set()
        
        # Notify of fallback
        self._dispatch_event(session_id, "textOutput", {
            "role": "ASSISTANT", 
            "content": "[SYSTEM] Bedrock Offline. Clinical Mock Mode Active."
        })
        # --- END MOCK MODE ---

    # ------------------------------------------------------------------
    # Response stream processing
    # ------------------------------------------------------------------

    async def _process_response_stream(self, session_id: str) -> None:
        """Process the response stream from Bedrock using the smithy SDK."""
        session = self._active_sessions.get(session_id)
        if session is None or session.stream is None:
            return

        try:
            while session.is_active:
                try:
                    output = await session.stream.await_output()
                    if not output or len(output) < 2 or output[1] is None:
                        continue
                    result = await output[1].receive()
                    value = getattr(result, "value", None)
                    if not value or not getattr(value, "bytes_", None):
                        continue

                    self._update_session_activity(session_id)
                    text_response = value.bytes_.decode("utf-8")
                    logger.info("[BEDROCK_RECV] %s", text_response)
                    try:
                        json_response = json.loads(text_response)
                        evt = json_response.get("event", {})

                        if evt.get("contentStart"):
                            self._dispatch_event(session_id, "contentStart", evt["contentStart"])
                            session.current_content_id = evt["contentStart"].get("contentId", "")
                        elif evt.get("completionStart"):
                            self._dispatch_event(session_id, "completionStart", evt["completionStart"])
                            # Track that assistant is speaking
                            session.assistant_speaking = True
                            session.completion_received = False
                            # NOTE: Do NOT clear active_tool_calls here — background tool tasks are still
                            # running and reference their tool IDs. They will clear themselves in their
                            # finally blocks. Only reset tool_call_pending if no tools are in flight.
                            if not session.active_tool_calls:
                                session.tool_call_pending = False
                            # Gracefully close the active audio input block of the user turn that just ended
                            if session.is_audio_content_start_sent:
                                old_id = session.audio_content_id
                                logger.info("completionStart: closing user audio block %s", old_id[:8])
                                # Set to False immediately BEFORE launching background task to avoid duplicate closes
                                session.is_audio_content_start_sent = False
                                session.is_audio_data_sent = False
                                session.open_content_ids.discard(old_id)
                                async def close_audio(target_id):
                                    async with session.write_lock:
                                        await self._send_event(session_id, {
                                            "event": {
                                                "contentEnd": {
                                                    "promptName": session.prompt_name,
                                                    "contentName": target_id,
                                                }
                                            }
                                        })
                                asyncio.create_task(close_audio(old_id))
                            # Reset user audio block state for the next turn
                            session.is_audio_content_start_sent = False
                            session.is_audio_data_sent = False
                            session.audio_content_id = str(uuid4())
                            # Keep audio paused during speech generation (tool responses will unpause when done)
                            session.audio_paused = True
                            logger.info("completionStart: paused audio for session %s (tools_in_flight=%d)", session_id[:8], len(session.active_tool_calls))
                        elif evt.get("textOutput"):
                            self._dispatch_event(session_id, "textOutput", evt["textOutput"])
                        elif evt.get("audioOutput"):
                            self._dispatch_event(session_id, "audioOutput", evt["audioOutput"])
                        elif evt.get("toolUse"):
                            self._dispatch_event(session_id, "toolUse", evt["toolUse"])
                            session.tool_call_pending = True
                            session.tool_use_content = evt["toolUse"]
                            session.tool_use_id = evt["toolUse"].get("toolUseId", "")
                            session.tool_name = evt["toolUse"].get("name") or evt["toolUse"].get("toolName", "")
                            
                            # Track tool call mapping by content block ID and add to active set
                            tool_id = evt["toolUse"].get("toolUseId", "")
                            if tool_id:
                                session.active_tool_calls.add(tool_id)
                            content_id = evt["toolUse"].get("contentId", "")
                            if content_id:
                                session.pending_tools[content_id] = evt["toolUse"]
                        elif (
                            evt.get("contentEnd")
                            and evt["contentEnd"].get("type") == "TOOL"
                        ):
                            logger.info("DEBUG: contentEnd event: %s", json.dumps(evt))
                            content_id = evt["contentEnd"].get("contentId", "")
                            tool_use = session.pending_tools.pop(content_id, None)
                            
                            # Dispatch toolEnd using the retrieved/matched tool call structure
                            t_use_content = tool_use if tool_use is not None else session.tool_use_content
                            t_use_id = t_use_content.get("toolUseId", "") if t_use_content else session.tool_use_id
                            t_name = (t_use_content.get("name") or t_use_content.get("toolName", "")) if t_use_content else session.tool_name
                            
                            self._dispatch_event(session_id, "toolEnd", {
                                "toolUseContent": t_use_content,
                                "toolUseId": t_use_id,
                                "toolName": t_name,
                            })
                            
                            # Asynchronously run tool execution and response logic to keep read loop non-blocking
                            async def run_tool_task(t_n, t_c, t_i):
                                try:
                                    external_result = await tool_processor(
                                        t_n.lower(),
                                        t_c.get("content", "{}") if isinstance(t_c, dict) else t_c,
                                        hospital_id=session.hospital_id,
                                    )
                                    await self._send_tool_result(session_id, t_i, external_result)
                                    self._dispatch_event(session_id, "toolResult", {
                                        "toolUseId": t_i,
                                        "result": external_result,
                                    })
                                except Exception:
                                    logger.exception("Error executing tool %s in background", t_n)
                                finally:
                                    # Discard from active calls; when ALL parallel tools are done, clean up state
                                    session.active_tool_calls.discard(t_i)
                                    remaining = len(session.active_tool_calls)
                                    logger.info("Tool %s finished. Remaining in-flight tools: %d", t_i[:8], remaining)
                                    if not session.active_tool_calls:
                                        # All parallel tools done — prepare a fresh audio block for the next user turn
                                        # and un-pause only after Nova's completionEnd has been received.
                                        # (completionEnd handler will check active_tool_calls and unpause.)
                                        session.tool_call_pending = False
                                        session.audio_content_id = str(uuid4())
                                        # If Nova already sent completionEnd while we were running tools,
                                        # audio_paused is still True (completionEnd saw tools in flight).
                                        # Unpause only if completion has been received.
                                        if session.completion_received:
                                            session.audio_paused = False
                                            logger.info("All parallel tool calls finished and completion received. Unpaused audio for session %s", session_id[:8])
                                        else:
                                            logger.info("All parallel tool calls finished but completion not yet received — will unpause at completionEnd for session %s", session_id[:8])
                                        
                                        # Safety unpause: if all tools done and audio still paused, force unpause
                                        # after a brief timeout. This handles any race condition or missed completionEnd.
                                        await asyncio.sleep(0.3)
                                        if session.audio_paused:
                                            session.audio_paused = False
                                            logger.info("Safety unpause: all parallel tools complete, force-unpaused audio for session %s", session_id[:8])
                            
                            # Start background execution task
                            asyncio.create_task(run_tool_task(t_name, t_use_content, t_use_id))
                        elif evt.get("usageEvent"):
                            ue = evt["usageEvent"]
                            details = ue.get("details", {})
                            total = details.get("total", {})
                            t_in = total.get("input", {})
                            t_out = total.get("output", {})
                            logger.info(
                                "[USAGE] session=%s | input: speech=%d text=%d | output: speech=%d text=%d | totalTokens=%d",
                                session_id[:8],
                                t_in.get("speechTokens", 0),
                                t_in.get("textTokens", 0),
                                t_out.get("speechTokens", 0),
                                t_out.get("textTokens", 0),
                                ue.get("totalTokens", 0),
                            )
                            self._dispatch_event(session_id, "usageEvent", ue)
                        elif evt.get("completionEnd"):
                            self._dispatch_event(session_id, "completionEnd", evt["completionEnd"])
                            # Assistant has finished speaking
                            session.assistant_speaking = False
                            session.completion_received = True
                            if session.active_tool_calls:
                                logger.info("completionEnd: tool call still in-flight (active=%d), keeping audio paused for session %s", len(session.active_tool_calls), session_id[:8])
                            else:
                                session.audio_paused = False
                                session.tool_call_pending = False
                                logger.info("completionEnd: no active tools in flight — unpaused audio for session %s", session_id[:8])
                        else:
                            event_keys = list(evt.keys())
                            if event_keys:
                                self._dispatch_event(
                                    session_id, event_keys[0], evt[event_keys[0]]
                                )
                            elif json_response:
                                self._dispatch_event(session_id, "unknown", json_response)
                    except (json.JSONDecodeError, ValueError):
                        logger.debug("Raw text response (parse error): %s", text_response)
                except StopAsyncIteration:
                    break
                except Exception as e:
                    if not session.is_active:
                        break
                    
                    err_msg = str(e)
                    if "403" in err_msg or "UnrecognizedClientException" in err_msg or "401" in err_msg:
                        logger.error("[BEDROCK] Critical Auth Error: %s. Terminating stream.", err_msg)
                        session.is_active = False 
                        raise # Re-raise to trigger Mock Fallback in initiate_session
                        
                    logger.exception("Error processing response chunk. Terminating stream to prevent infinite busy loop.")
                    session.is_active = False
                    break

            self._dispatch_event(session_id, "streamComplete", {
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as error:
            self._dispatch_event(session_id, "error", {
                "source": "responseStream",
                "message": "Error processing response stream",
                "details": str(error),
            })

    # ------------------------------------------------------------------
    # Tool result sending
    # ------------------------------------------------------------------

    async def _send_tool_result(self, session_id: str, tool_use_id: str, result: Any) -> None:
        """Send a tool result back to Bedrock.
        
        For parallel tool calls, multiple background tasks may call this concurrently.
        The write_lock serializes them. Only the FIRST caller closes the audio block;
        subsequent ones skip the close (is_audio_content_start_sent is already False).
        We do NOT touch audio_content_id here — that is managed by the caller
        (run_tool_task's finally block) after ALL tools finish, to avoid stomping.
        """
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active:
            return

        # Pause audio ingestion so no new audio events race with tool result submission
        session.audio_paused = True

        async with session.write_lock:
            # AWS Nova Sonic: Close the active audio content block before sending TOOL results.
            # Bidirectional streaming only allows one open content block at a time.
            # Only the first parallel task will find is_audio_content_start_sent=True.
            if session.is_audio_content_start_sent:
                logger.info("_send_tool_result[%s]: closing open audio block %s", tool_use_id[:8], session.audio_content_id[:8])
                await self._send_event(session_id, {
                    "event": {
                        "contentEnd": {
                            "promptName": session.prompt_name,
                            "contentName": session.audio_content_id,
                        }
                    }
                })
                session.is_audio_content_start_sent = False
                session.is_audio_data_sent = False
                session.open_content_ids.discard(session.audio_content_id)
                # Brief pause to let Bedrock process the contentEnd before the TOOL block
                await asyncio.sleep(0.2)

            # Each tool result gets its own isolated content block ID
            content_id = str(uuid4())
            logger.info("_send_tool_result[%s]: sending tool result in content block %s", tool_use_id[:8], content_id[:8])

            await self._send_event(session_id, {
                "event": {
                    "contentStart": {
                        "promptName": session.prompt_name,
                        "contentName": content_id,
                        "interactive": False,
                        "type": "TOOL",
                        "toolResultInputConfiguration": {
                            "toolUseId": tool_use_id,
                            "type": "TEXT",
                            "textInputConfiguration": {"mediaType": "text/plain"},
                        },
                    }
                }
            })

            result_content = result if isinstance(result, str) else json.dumps(result)
            await self._send_event(session_id, {
                "event": {
                    "toolResult": {
                        "promptName": session.prompt_name,
                        "contentName": content_id,
                        "content": result_content,
                        "role": "TOOL",
                    }
                }
            })

            await self._send_event(session_id, {
                "event": {
                    "contentEnd": {
                        "promptName": session.prompt_name,
                        "contentName": content_id,
                    }
                }
            })
            logger.info("_send_tool_result[%s]: tool result block %s sent successfully", tool_use_id[:8], content_id[:8])

    # ------------------------------------------------------------------
    # Session setup events (now async — send directly to stream)
    # ------------------------------------------------------------------

    async def setup_prompt_start_event(self, session_id: str) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            return

        async with session.write_lock:
            audio_out = DEFAULT_AUDIO_OUTPUT_CONFIG
            voice_id = (audio_out.voice_id or os.environ.get("NOVA_VOICE_ID", "tiffany")).strip().lower()
            await self._send_event(session_id, {
                "event": {
                    "promptStart": {
                        "promptName": session.prompt_name,
                        "textOutputConfiguration": {"mediaType": "text/plain"},
                        "audioOutputConfiguration": {
                            "audioType": audio_out.audio_type,
                            "mediaType": audio_out.media_type,
                            "sampleRateHertz": audio_out.sample_rate_hertz,
                            "sampleSizeBits": audio_out.sample_size_bits,
                            "channelCount": audio_out.channel_count,
                            "encoding": audio_out.encoding,
                            "voiceId": voice_id,
                        },
                        "toolUseOutputConfiguration": {"mediaType": "application/json"},
                        "toolConfiguration": {"tools": available_tools},
                    }
                }
            })
            session.is_prompt_start_sent = True

    async def setup_system_prompt_event(
        self,
        session_id: str,
        text_config: TextConfiguration = None,
        system_prompt: str = None,
    ) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            return

        async with session.write_lock:
            if text_config is None:
                text_config = DEFAULT_TEXT_CONFIG
            if system_prompt is None:
                system_prompt = DEFAULT_SYSTEM_PROMPT

            text_prompt_id = str(uuid4())
            session.open_content_ids.add(text_prompt_id)

            await self._send_event(session_id, {
                "event": {
                    "contentStart": {
                        "promptName": session.prompt_name,
                        "contentName": text_prompt_id,
                        "type": "TEXT",
                        "interactive": True,
                        "role": "SYSTEM",
                        "textInputConfiguration": {
                            "mediaType": text_config.media_type,
                        },
                    }
                }
            })
            await self._send_event(session_id, {
                "event": {
                    "textInput": {
                        "promptName": session.prompt_name,
                        "contentName": text_prompt_id,
                        "content": system_prompt,
                        "role": "SYSTEM",
                    }
                }
            })
            await self._send_event(session_id, {
                "event": {
                    "contentEnd": {
                        "promptName": session.prompt_name,
                        "contentName": text_prompt_id,
                    }
                }
            })
            session.open_content_ids.discard(text_prompt_id)

    async def setup_start_audio_event(
        self, session_id: str, audio_config: AudioConfiguration = None, acquire_lock: bool = True
    ) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            return
        if audio_config is None:
            audio_config = DEFAULT_AUDIO_INPUT_CONFIG

        async def _send():
            await self._send_event(session_id, {
                "event": {
                    "contentStart": {
                        "promptName": session.prompt_name,
                        "contentName": session.audio_content_id,
                        "type": "AUDIO",
                        "interactive": True,
                        "audioInputConfiguration": {
                            "audioType": audio_config.audio_type,
                            "mediaType": audio_config.media_type,
                            "sampleRateHertz": audio_config.sample_rate_hertz,
                            "sampleSizeBits": audio_config.sample_size_bits,
                            "channelCount": audio_config.channel_count,
                            "encoding": audio_config.encoding,
                        },
                    }
                }
            })
            session.is_audio_content_start_sent = True
            session.open_content_ids.add(session.audio_content_id)

        if acquire_lock:
            async with session.write_lock:
                await _send()
        else:
            await _send()

    async def stream_audio_chunk(self, session_id: str, audio_data: bytes) -> None:
        """Base64-encode audio data and send an audioInput event."""
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active or not session.audio_content_id:
            logger.debug("Session %s is inactive or has no audio_content_id, ignoring stream_audio_chunk call.", session_id)
            return
        # Skip if audio is paused (idle follow-up in progress)
        if session.audio_paused:
            return

        async with session.write_lock:
            if session.audio_paused or not session.is_active:
                return

            if not session.is_audio_content_start_sent:
                await self.setup_start_audio_event(session_id, acquire_lock=False)

            base64_data = base64.b64encode(audio_data).decode("ascii")
            session.is_audio_data_sent = True
            session.audio_ever_sent = True
            await self._send_event(session_id, {
                "event": {
                    "audioInput": {
                        "promptName": session.prompt_name,
                        "contentName": session.audio_content_id,
                        "content": base64_data,
                        "role": "USER",
                    }
                }
            })

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def send_content_end(self, session_id: str, acquire_lock: bool = True) -> None:
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_audio_content_start_sent:
            logger.info("send_content_end: skipping as audio content was never started")
            return

        async def _send():
            if not session.is_audio_data_sent:
                logger.info("send_content_end: skipping empty audio content %s", session.audio_content_id[:8])
                session.is_audio_content_start_sent = False
                session.open_content_ids.discard(session.audio_content_id)
                return
                
            await self._send_event(session_id, {
                "event": {
                    "contentEnd": {
                        "promptName": session.prompt_name,
                        "contentName": session.audio_content_id,
                    }
                }
            })
            session.is_audio_content_start_sent = False
            session.is_audio_data_sent = False
            session.open_content_ids.discard(session.audio_content_id)
            await asyncio.sleep(0.5)

        if acquire_lock:
            async with session.write_lock:
                await _send()
        else:
            await _send()

    async def send_text_message(self, session_id: str, text: str) -> None:
        """Inject a cross-modal text message into the active session stream.

        Per AWS docs, cross-modal text input must use role=USER and interactive=true.
        The textInput event itself should NOT include a role field.

        Pauses audio input, closes the current audio content, sends the text,
        then re-opens audio input. This is required because Nova Sonic cannot
        process a new content block while an audio content block is still open.
        """
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active:
            return

        async with session.write_lock:
            logger.info("send_text_message: pausing audio and closing audio content")

            # 1. Pause audio ingestion so no more audioInput events are sent
            session.audio_paused = True
            session.completion_received = False
            # Wait long enough for any in-flight _process_audio_queue batch to finish
            await asyncio.sleep(0.5)

            # 2. Close current audio content block if it was opened.
            # AWS Nova Sonic requires every content block to be closed before a new
            # prompt/content block is started, even when no audio bytes were sent.
            old_audio_id = session.audio_content_id
            if session.is_audio_content_start_sent and session.is_audio_data_sent:
                await self._send_event(session_id, {
                    "event": {
                        "contentEnd": {
                            "promptName": session.prompt_name,
                            "contentName": old_audio_id,
                        }
                    }
                })
                logger.info("send_text_message: closed audio content %s", old_audio_id[:8])
                # Give Nova time to process the audio content closure
                await asyncio.sleep(0.5)
                session.is_audio_content_start_sent = False
                session.is_audio_data_sent = False # Reset for next block
                session.open_content_ids.discard(old_audio_id)
            elif session.is_audio_content_start_sent:
                logger.info("send_text_message: skipping closure of empty audio content %s", old_audio_id[:8])
                session.is_audio_content_start_sent = False
                session.is_audio_data_sent = False
                session.open_content_ids.discard(old_audio_id)

            # 3. Send the text message as cross-modal USER text input (per AWS docs)
            content_id = str(uuid4())
            logger.info("send_text_message: sending cross-modal text content %s", content_id[:8])
            text_started = False
            try:
                # 3a. Send the text content block
                await self._send_event(session_id, {
                    "event": {
                        "contentStart": {
                            "promptName": session.prompt_name,
                            "contentName": content_id,
                            "type": "TEXT",
                            "interactive": True,
                            "role": "USER",
                            "textInputConfiguration": {
                                "mediaType": "text/plain",
                            },
                        }
                    }
                })
                session.open_content_ids.add(content_id)
                text_started = True

                await self._send_event(session_id, {
                    "event": {
                        "textInput": {
                            "promptName": session.prompt_name,
                            "contentName": content_id,
                            "content": text,
                        }
                    }
                })
                await self._send_event(session_id, {
                    "event": {
                        "contentEnd": {
                            "promptName": session.prompt_name,
                            "contentName": content_id,
                        }
                    }
                })
                session.open_content_ids.discard(content_id)
                logger.info("send_text_message: text content sent")

                # 3b. Pre-open the new audio content block and send one silent chunk immediately.
                # This ensures the session has an active audio channel ready for the caller's live audio.
                # It also satisfies Nova Sonic's requirement of having at least one audio content block
                # in the session to process.
                new_audio_id = str(uuid4())
                session.audio_content_id = new_audio_id
                await self._send_event(session_id, {
                    "event": {
                        "contentStart": {
                            "promptName": session.prompt_name,
                            "contentName": new_audio_id,
                            "type": "AUDIO",
                            "interactive": True,
                            "role": "USER",
                            "audioInputConfiguration": {
                                "mediaType": "audio/lpcm",
                                "sampleRateHertz": 8000,
                                "sampleSizeBits": 16,
                                "channelCount": 1,
                                "encoding": "base64",
                            }
                        }
                    }
                })
                session.is_audio_content_start_sent = True
                session.open_content_ids.add(new_audio_id)

                # Send 100ms of silence to initialize the audio stream
                silence_b64 = base64.b64encode(b'\x00' * 1600).decode("utf-8")
                await self._send_event(session_id, {
                    "event": {
                        "audioInput": {
                            "promptName": session.prompt_name,
                            "contentName": new_audio_id,
                            "content": silence_b64,
                            "role": "USER",
                        }
                    }
                })
                session.is_audio_data_sent = True
                logger.info("send_text_message: pre-opened audio content %s and sent initial silence", new_audio_id[:8])

            finally:
                if text_started and content_id in session.open_content_ids:
                    try:
                        await self._send_event(session_id, {
                            "event": {
                                "contentEnd": {
                                    "promptName": session.prompt_name,
                                    "contentName": content_id,
                                }
                            }
                        })
                        logger.info("send_text_message: forcibly closed text content %s", content_id[:8])
                    except Exception:
                        logger.exception("Failed to close cross-modal text content %s", content_id[:8])
                    session.open_content_ids.discard(content_id)

                # Fallback: if audio content block wasn't opened (e.g. exception during text send), prepare one
                if not session.is_audio_content_start_sent:
                    session.audio_content_id = str(uuid4())
                    session.is_audio_data_sent = False
                    logger.info("send_text_message: fallback prepared new audio content %s", session.audio_content_id[:8])

                # 5. Resume audio ingestion
                session.audio_paused = False

    async def _close_all_open_contents(self, session_id: str, acquire_lock: bool = True) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            return

        async def _close():
            for content_id in list(session.open_content_ids):
                if content_id == session.audio_content_id and not session.is_audio_data_sent:
                    logger.info("_close_all_open_contents: skipping empty audio content %s", content_id[:8])
                    session.is_audio_content_start_sent = False
                    session.open_content_ids.discard(content_id)
                    continue
                try:
                    await self._send_event(session_id, {
                        "event": {
                            "contentEnd": {
                                "promptName": session.prompt_name,
                                "contentName": content_id,
                            }
                        }
                    })
                    logger.info("_close_all_open_contents: closed content %s", content_id[:8])
                except Exception:
                    logger.exception("_close_all_open_contents: failed to close content %s", content_id[:8])
                finally:
                    if content_id == session.audio_content_id:
                        session.is_audio_content_start_sent = False
                        session.is_audio_data_sent = False
                    session.open_content_ids.discard(content_id)

        if acquire_lock:
            async with session.write_lock:
                await _close()
        else:
            await _close()

    async def send_prompt_end(self, session_id: str, acquire_lock: bool = True) -> None:
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_prompt_start_sent:
            return

        async def _send():
            await self._close_all_open_contents(session_id, acquire_lock=False)
            await self._send_event(session_id, {
                "event": {
                    "promptEnd": {
                        "promptName": session.prompt_name,
                    }
                }
            })
            await asyncio.sleep(0.3)

        if acquire_lock:
            async with session.write_lock:
                await _send()
        else:
            await _send()

    async def send_session_end(self, session_id: str, acquire_lock: bool = True) -> None:
        session = self._active_sessions.get(session_id)
        if session is None:
            return

        async def _send():
            await self._send_event(session_id, {
                "event": {"sessionEnd": {}}
            })
            await asyncio.sleep(0.3)
            session.is_active = False
            # Close the stream
            try:
                if session.stream:
                    await session.stream.input_stream.close()
            except Exception:
                logger.debug("Failed to close stream input in send_session_end", exc_info=True)
            self._active_sessions.pop(session_id, None)
            self._session_last_activity.pop(session_id, None)

        if acquire_lock:
            async with session.write_lock:
                await _send()
        else:
            await _send()

    async def close_session(self, session_id: str) -> None:
        """Gracefully close a session with the full shutdown sequence."""
        if session_id in self._session_cleanup_in_progress:
            return
        self._session_cleanup_in_progress.add(session_id)
        try:
            session = self._active_sessions.get(session_id)
            if session is None:
                return

            async with session.write_lock:
                if not session.audio_ever_sent:
                    logger.info("close_session: closing no-audio session without Bedrock end events")
                    session.is_active = False
                    try:
                        if session.stream:
                            await session.stream.input_stream.close()
                    except Exception:
                        logger.debug("Failed to close no-audio stream input", exc_info=True)
                    self._active_sessions.pop(session_id, None)
                    self._session_last_activity.pop(session_id, None)
                    return

                await self.send_content_end(session_id, acquire_lock=False)
                session = self._active_sessions.get(session_id)
                if session is not None and session.audio_ever_sent:
                    await self.send_prompt_end(session_id, acquire_lock=False)
                else:
                    logger.info("close_session: skipping promptEnd because no caller audio was sent")
                await self.send_session_end(session_id, acquire_lock=False)
        except Exception:
            logger.exception("Error during graceful close for session %s", session_id)
            session = self._active_sessions.get(session_id)
            if session is not None:
                session.is_active = False
                try:
                    if session.stream:
                        await session.stream.input_stream.close()
                except Exception:
                    logger.debug("Failed to close stream input during forceful cleanup", exc_info=True)
                self._active_sessions.pop(session_id, None)
                self._session_last_activity.pop(session_id, None)
        finally:
            self._session_cleanup_in_progress.discard(session_id)

    def force_close_session(self, session_id: str) -> None:
        """Force-close a session immediately without sending end events."""
        if session_id in self._session_cleanup_in_progress or session_id not in self._active_sessions:
            return
        self._session_cleanup_in_progress.add(session_id)
        try:
            session = self._active_sessions.get(session_id)
            if session is None:
                return
            session.is_active = False
            self._active_sessions.pop(session_id, None)
            self._session_last_activity.pop(session_id, None)
        finally:
            self._session_cleanup_in_progress.discard(session_id)

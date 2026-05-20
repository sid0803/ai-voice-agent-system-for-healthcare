import asyncio
import base64
import json
import os
import logging
import pathlib
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from src.nova_client import S2SBidirectionalStreamClient
from src.audio_utils import exotel_to_pcm, pcm_to_exotel, AudioHardener, AudioPolisher
from dotenv import load_dotenv

try:
    from silero_vad import load_silero_vad
    import torch
except ImportError:
    load_silero_vad = None
    torch = None

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Audio Utilities
hardener = AudioHardener()
polisher = AudioPolisher()

# Load pre-recorded greeting placeholder ("hlw")
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
try:
    hello_audio_bytes = (_PROJECT_ROOT / "assets" / "hello.pcm").read_bytes()
except Exception:
    logger.warning("Missing hello.pcm, using silence")
    hello_audio_bytes = b'\x00' * 16000

# Initialize Bedrock Client
bedrock_client = S2SBidirectionalStreamClient(
    region=os.environ.get("BEDROCK_REGION", "us-east-1"),
    credentials={
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY")
    }
)

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/incoming-call")
async def incoming_call(request: Request):
    print("📞 --- CALL RECEIVED (STEP 1: HANDSHAKE) ---")
    host = request.headers.get("host", "localhost")
    ws_url = f"wss://{host}/exotel-stream"
    print(f"🔗 Returning WS URL: {ws_url}")
    return JSONResponse(content={"url": ws_url})

@app.websocket("/exotel-stream")
async def exotel_stream(websocket: WebSocket):
    print("🔌 --- WEBSOCKET ATTEMPT (STEP 2: CONNECTING) ---")
    await websocket.accept()
    print("✅ --- WEBSOCKET CONNECTED! ASHA IS LIVE ---")

    session_id = str(uuid4())
    session = bedrock_client.create_stream_session(session_id)
    
    if load_silero_vad is None or torch is None:
        raise RuntimeError(
            "silero-vad and torch must be installed to use Silero turn detection. "
            "Install with: pip install silero-vad"
        )

    vad_model = load_silero_vad()
    vad_buffer = b""
    VAD_CHUNK_SAMPLES = 512
    VAD_CHUNK_BYTES = VAD_CHUNK_SAMPLES * 2
    SILENCE_CHUNKS_NEEDED = 12
    SPEECH_CHUNKS_NEEDED = 4
    speech_chunk_count = 0
    silence_chunk_count = 0
    is_speaking = False
    turn_active = False

    stream_sid = ""
    bedrock_ready = False

    def handle_text(data):
        text = data.get("content", "")
        if text:
            print(f"\n🤖 ASHA: {text}")

    def handle_audio(data):
        print(f"🔊 NOVA OUTPUT AUDIO: {len(data.get('content', ''))} chars")
        async def _send():
            try:
                if not stream_sid:
                    return
                pcm_bytes = base64.b64decode(data["content"])
                polished_bytes = polisher.process_chunk(pcm_bytes)
                ex_bytes = pcm_to_exotel(polished_bytes)
                payload_b64 = base64.b64encode(ex_bytes).decode("utf-8")
                
                await websocket.send_text(json.dumps({
                    "event": "media",
                    "stream_sid": stream_sid,
                    "media": {"payload": payload_b64}
                }))
            except Exception as e:
                print(f"⚠️ Error sending audio: {e}")
        asyncio.ensure_future(_send())

    session.on_event("textOutput", handle_text)
    session.on_event("audioOutput", handle_audio)
    
    # 1. Start Bedrock session in background
    asyncio.create_task(bedrock_client.initiate_session(session_id))

    try:
        while True:
            message = await websocket.receive()
            
            if "text" in message:
                data = json.loads(message["text"])
                event_type = data.get("event")
                
                if event_type == "media":
                    if bedrock_ready:
                        payload = data.get("media", {}).get("payload", "")
                        if payload:
                            try:
                                raw_bytes = base64.b64decode(payload)
                                pcm_samples = exotel_to_pcm(raw_bytes)
                                hardened_pcm = hardener.process_chunk(pcm_samples)
                                vad_buffer += hardened_pcm
                                while len(vad_buffer) >= VAD_CHUNK_BYTES:
                                    chunk = vad_buffer[:VAD_CHUNK_BYTES]
                                    vad_buffer = vad_buffer[VAD_CHUNK_BYTES:]
                                    audio_f32 = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                                    tensor = torch.from_numpy(audio_f32)
                                    speech_prob = vad_model(tensor, 8000).item()
                                    is_speech_frame = speech_prob > 0.5

                                    if is_speech_frame:
                                        speech_chunk_count += 1
                                        silence_chunk_count = 0
                                        if not is_speaking and speech_chunk_count >= SPEECH_CHUNKS_NEEDED:
                                            is_speaking = True
                                            turn_active = True
                                            print(f"🗣️ Speech detected (prob={speech_prob:.2f})")
                                    else:
                                        silence_chunk_count += 1
                                        speech_chunk_count = 0
                                        if is_speaking and silence_chunk_count >= SILENCE_CHUNKS_NEEDED:
                                            is_speaking = False
                                            if turn_active:
                                                turn_active = False
                                                print("🔇 End of turn — signalling Nova")
                                                await session.end_audio_content()
                                                await asyncio.sleep(0.1)
                                                session.audio_content_id = str(uuid4())
                                                await bedrock_client.setup_start_audio_event(session_id)
                                                print("🎤 Audio reopened for next turn")

                                if is_speaking or turn_active:
                                    print(f"📤 Forwarding audio chunk to Nova Sonic ({len(hardened_pcm)} bytes PCM)")
                                    await session.stream_audio(hardened_pcm)
                            except Exception as e:
                                print(f"⚠️ Audio conversion / VAD error: {e}")

                elif event_type == "start":
                    stream_sid = data.get("start", {}).get("stream_sid", "") or data.get("stream_sid", "")
                    print(f"🎙️ Stream Started! SID: {stream_sid}")
                    
                    # 1. Send placeholder greeting audio ("hlw") immediately to keep Exotel alive
                    try:
                        polished_greeting = polisher.process_chunk(hello_audio_bytes)
                        greeting_b64 = base64.b64encode(polished_greeting).decode("utf-8")
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "stream_sid": stream_sid,
                            "media": {"payload": greeting_b64}
                        }))
                        print("📢 Placeholder audio sent to keep line open...")
                    except Exception as e:
                        print(f"⚠️ Failed to send placeholder audio: {e}")

                    # 2. Wait for Bedrock stream to open precisely
                    session_data = bedrock_client._active_sessions.get(session_id)
                    if session_data and hasattr(session_data, "_stream_ready"):
                        try:
                            await asyncio.wait_for(session_data._stream_ready.wait(), timeout=10.0)
                            await asyncio.sleep(0.3) # Allow sessionStart to fully transmit
                        except asyncio.TimeoutError:
                            print("⚠️ Bedrock stream ready timeout, proceeding anyway")
                    else:
                        await asyncio.sleep(2)
                    
                    # 3. Configure Bedrock Prompt & Audio
                    print("⚙️ --- SETTING UP ASHA'S BRAIN ---")
                    await session.setup_prompt_start()
                    await session.setup_system_prompt()
                    await session.setup_start_audio()

                    # Trigger Bedrock to generate the greeting dynamically in Asha's persona
                    greeting_trigger = "[The caller has just connected. Welcome them back warmly if context shows their name, otherwise welcome them as a new caller to InDiiServe Healthcare, introduce yourself as Asha, and ask how you can assist them today.]"
                    asyncio.create_task(bedrock_client.send_text_message(session_id, greeting_trigger))
                    
                    bedrock_ready = True
                    print("🧠 Bedrock is ready — audio will now be forwarded")
                    print("\n>>> [SYSTEM] Bedrock ready! Speak into your phone now: 'Hello Asha'")
                
                elif event_type == "stop":
                    print("🛑 Exotel sent STOP event. Draining in-flight Bedrock audio...")
                    await asyncio.sleep(1.5) # Give Bedrock time to finish streaming its response
                    break
                    
    except Exception as e:
        print(f"❌ Connection Closed: {e}")
    finally:
        await session.close()
        print("🚩 Session Closed.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)

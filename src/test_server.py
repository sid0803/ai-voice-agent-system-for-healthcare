import asyncio
import base64
import json
import os
import logging
import pathlib
from uuid import uuid4
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from src.nova_client import S2SBidirectionalStreamClient
from src.audio_utils import exotel_to_pcm, pcm_to_exotel, AudioHardener, AudioPolisher
from dotenv import load_dotenv

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
                                print(f"📤 Forwarding audio chunk to Nova Sonic ({len(pcm_samples)} bytes PCM)")
                                # Apply Noise Gate to mute cellular background hum so turn detection triggers instantly
                                hardened_pcm = hardener.process_chunk(pcm_samples)
                                await session.stream_audio(hardened_pcm)
                            except Exception as e:
                                print(f"⚠️ Audio conversion error: {e}")

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

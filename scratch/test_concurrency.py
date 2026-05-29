import asyncio
import json
import logging
import os
import subprocess
import time
import websockets
import sys
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("test_concurrency")

async def run_test():
    # Start server in background in Demo Mode
    logger.info("Starting Asha Server...")
    env = os.environ.copy()
    env["DEMO_MODE"] = "true"
    env["HOSPITAL_ID"] = "apollo_metro"
    env["PYTHONPATH"] = "."
    env["AWS_EC2_METADATA_DISABLED"] = "true"

    # Load .env
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in env:
                        env[key] = val

    server_log = open("server_concurrency_test.log", "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.server"],
        env=env,
        cwd=os.getcwd(),
        stdout=server_log,
        stderr=server_log
    )

    try:
        # Wait for server
        logger.info("Waiting for server to start...")
        import httpx
        async with httpx.AsyncClient() as client:
            for _ in range(15):
                try:
                    resp = await client.get("http://127.0.0.1:8000/health", timeout=1.0)
                    if resp.status_code == 200:
                        logger.info("Server is running.")
                        break
                except:
                    pass
                await asyncio.sleep(1)
            else:
                logger.error("Server failed to start.")
                return

        # Connect
        token = env.get("EXOTEL_WS_SECRET", "")
        uri = f"ws://127.0.0.1:8000/exotel-stream?hospital_id=apollo_metro&CallSid={uuid4()}&token={token}"
        logger.info(f"Connecting to WebSocket: {uri}")
        
        async with websockets.connect(uri) as ws:
            # Send start event
            await ws.send(json.dumps({
                "event": "start",
                "start": {
                    "hospital_id": "apollo_metro",
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4())
                }
            }))

            # Helper to read response
            async def read_response(timeout=8.0):
                response_text = ""
                tools_triggered = []
                start_time = time.time()
                while time.time() - start_time < timeout:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        data = json.loads(msg)
                        event = data.get("event")
                        if event == "text":
                            txt = data.get("text", "")
                            if txt.strip() and not txt.strip().startswith("{"):
                                response_text += " " + txt.strip()
                                start_time = time.time()
                        elif event == "tool":
                            tools_triggered.append(data.get("name"))
                            start_time = time.time()
                    except asyncio.TimeoutError:
                        if response_text.strip():
                            break
                return response_text.strip(), tools_triggered

            # Get Greeting
            greeting, _ = await read_response(10.0)
            logger.info(f"Greeting: {greeting}")

            # Send combined query
            query = "can you tell me about the cafeteria and the pharmacy?"
            logger.info(f"Sending combined query: '{query}'")
            await ws.send(json.dumps({
                "type": "chat",
                "text": query
            }))

            response, tools = await read_response(30.0)
            logger.info(f"Tools triggered: {tools}")
            logger.info(f"Response: {response}")

            # Verify that both tool calls were processed
            # In clinical mock mode:
            # "where", "location", "pharmacy" triggers hospitalInfoTool
            # Since the mock mode routes "pharmacy" -> hospitalInfoTool, let's see.
            # In production mode (using real Bedrock), Bedrock will call hospitalInfoTool for cafeteria and hospitalInfoTool for pharmacy.
            # Let's see if we get the expected behavior.
            
    finally:
        logger.info("Terminating server...")
        server_proc.terminate()
        server_log.close()

if __name__ == "__main__":
    asyncio.run(run_test())

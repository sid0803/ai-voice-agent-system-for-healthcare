import asyncio
import json
import os
import subprocess
import sys
import websockets
from uuid import uuid4

async def test_queries():
    print("Starting server for test_user_queries...")
    env = os.environ.copy()
    env["DEMO_MODE"] = "true"
    env["HOSPITAL_ID"] = "apollo_metro"
    env["PYTHONPATH"] = "."
    env["AWS_EC2_METADATA_DISABLED"] = "true"

    # Explicitly load .env file into subprocess environment so boto3 gets credentials
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

    f = open("server_startup_test.log", "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.server"],
        env=env,
        cwd=os.getcwd(),
        stdout=f,
        stderr=f
    )

    import httpx
    async with httpx.AsyncClient() as client:
        for i in range(30):
            try:
                resp = await client.get("http://127.0.0.1:8000/health", timeout=1.0)
                if resp.status_code == 200:
                    print("Server is up and healthy.")
                    break
            except:
                pass
            await asyncio.sleep(1)
        else:
            print("Server failed to start.")
            server_proc.terminate()
            f.close()
            return

    uri = f"ws://127.0.0.1:8000/exotel-stream?hospital_id=apollo_metro&CallSid={uuid4()}"

    queries = [
        "what about the mri?",
        "can you tell me the pricing of thyroid?",
        "is there any parking available?"
    ]

    try:
        async with websockets.connect(uri) as websocket:
            # Send start event
            await websocket.send(json.dumps({
                "event": "start",
                "start": {
                    "hospital_id": "apollo_metro",
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4())
                }
            }))

            # Drain greeting
            greeting = ""
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data.get("event") == "text":
                    greeting = data.get("text")
                    print(f"Greeting: {greeting}")
                    break

            for q in queries:
                print(f"\nSending Query: '{q}'")
                await websocket.send(json.dumps({
                    "type": "chat",
                    "text": q
                }))

                start_time = time_time = asyncio.get_event_loop().time()
                tool_called = False
                text_response = ""
                while asyncio.get_event_loop().time() - start_time < 15:
                    try:
                        msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
                    data = json.loads(msg)
                    if data.get("event") == "tool":
                        print(f"  Tool triggered: {data.get('name')} with args: {data.get('args')}")
                        tool_called = True
                    elif data.get("event") == "text":
                        text_response += data.get("text", "")
                        print(f"  AI Response Chunk: {data.get('text')}")

                print(f"Final AI Response for '{q}': {text_response}")
                if tool_called:
                    print(f"SUCCESS: Tool called for query '{q}'")
                else:
                    # If tool wasn't called but the response contains the correct info (like MRI price or parking rates), it's also a pass
                    print(f"INFO: No tool called. Response contained correct info? {any(x in text_response for x in ['8,500', '8500', '750', '30', 'parking', 'Basement'])}")

    except Exception as e:
        print(f"Error during test: {e}")
    finally:
        print("Stopping server...")
        server_proc.terminate()
        server_proc.wait()
        f.close()

if __name__ == "__main__":
    asyncio.run(test_queries())

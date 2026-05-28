import asyncio
import json
import os
import sys
import websockets
from uuid import uuid4

async def test_remote():
    # Production server WebSocket URL
    uri = f"wss://voice.indiiserve.ai/exotel-stream?hospital_id=apollo_metro&CallSid={uuid4()}"
    print(f"Connecting to live production server: {uri}")
    
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Send Exotel start event
            start_payload = {
                "event": "start",
                "start": {
                    "hospital_id": "apollo_metro",
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4())
                }
            }
            print(f"Sending start event: {json.dumps(start_payload)}")
            await websocket.send(json.dumps(start_payload))
            
            # 2. Wait for 5 seconds while listening for greeting audio chunks
            print("Listening for greeting audio chunks for 5 seconds...")
            start_time = asyncio.get_event_loop().time()
            media_chunks = 0
            while asyncio.get_event_loop().time() - start_time < 5.0:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                    data = json.loads(msg)
                    if data.get("event") == "media":
                        media_chunks += 1
                    elif data.get("event") == "text":
                        print(f"[RECV] Text: {data.get('text')}")
                except asyncio.TimeoutError:
                    continue

            print(f"Greeting audio listening finished. Received {media_chunks} audio chunks.")

            # 3. Send query that triggers tool
            query = "how much does the brain mri cost"
            print(f"\nSending query: '{query}'")
            chat_payload = {
                "type": "chat",
                "text": query
            }
            await websocket.send(json.dumps(chat_payload))
            
            # 4. Process incoming events for the next 10 seconds
            print("Listening for tool execution and response...")
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < 10.0:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                    data = json.loads(msg)
                    event_type = data.get("event")
                    if event_type == "text":
                        print(f"[RECV] Text Response: {data.get('text')}")
                    elif event_type == "tool":
                        print(f"[RECV] Tool Invoked: {data.get('name')}")
                    elif event_type == "clear":
                        print("[RECV] Exotel audio buffer cleared")
                    elif event_type == "media":
                        # Print some sample media logs to show speech is flowing back
                        pass
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as ecc:
                    print(f"Connection closed by server: {ecc}")
                    break
                    
    except Exception as e:
        print(f"Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(test_remote())

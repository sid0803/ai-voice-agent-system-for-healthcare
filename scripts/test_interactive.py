import asyncio
import json
import os
import sys
import websockets
from uuid import uuid4

# Color Codes
GREEN = '\033[92m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

async def receive_messages(websocket):
    """Receive and print messages from the server in real-time."""
    try:
        async for message in websocket:
            data = json.loads(message)
            event = data.get("event")
            
            if event == "text":
                text = data.get("text", "")
                if text.strip():
                    print(f"\n{GREEN}[Asha]:{RESET} {text}")
            elif event == "tool":
                tool_name = data.get("name", "")
                tool_args = data.get("args", {})
                print(f"\n{BLUE}[TOOL CALL]:{RESET} AI triggered tool {YELLOW}{tool_name}{RESET} with args: {tool_args}")
            elif event == "media":
                # Media chunk received (telephony audio payload)
                pass
            elif event == "mark":
                # Audio play mark
                pass
    except websockets.exceptions.ConnectionClosed:
        print(f"\n{RED}[System]: Connection closed by server.{RESET}")
    except Exception as e:
        print(f"\n{RED}[System Error]: {e}{RESET}")

async def send_messages(websocket):
    """Read console input and send chat messages to the server."""
    print(f"\n{BLUE}=== ASHA INTERACTIVE REAL-TIME CHAT ==={RESET}")
    print(f"You can now converse with Asha in real-time.")
    print(f"Type your message and press Enter. Type {RED}'exit'{RESET} to quit.\n")
    
    try:
        while True:
            # Read input from standard input asynchronously
            user_input = await asyncio.to_thread(input, f"{BLUE}[You]:{RESET} ")
            if user_input.strip().lower() == "exit":
                break
            
            if not user_input.strip():
                continue
                
            await websocket.send(json.dumps({
                "type": "chat",
                "text": user_input
            }))
            # Brief sleep to let the prompt print after output
            await asyncio.sleep(0.1)
    except Exception as e:
        print(f"\n{RED}[System Error]: {e}{RESET}")

async def run_chat():
    # Allow target server override via command line arguments
    server_host = "127.0.0.1:8000"
    if len(sys.argv) > 1:
        server_host = sys.argv[1]
        
    hospital_id = "apollo_metro"
    if len(sys.argv) > 2:
        hospital_id = sys.argv[2]

    uri = server_host if "://" in server_host else f"ws://{server_host}"
    if "/exotel-stream" not in uri:
        # Append path if not present
        if "?" in uri:
            base_part, query_part = uri.split("?", 1)
            uri = f"{base_part.rstrip('/')}/exotel-stream?{query_part}"
        else:
            uri = f"{uri.rstrip('/')}/exotel-stream"
            
    # Append query parameters if not present
    if "hospital_id=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}hospital_id={hospital_id}"
    if "CallSid=" not in uri:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}CallSid={uuid4()}"
    
    print(f"Connecting to Asha voice agent at {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"{GREEN}[Connected]{RESET} Initializing conversation...")
            
            # Send initial start event
            await websocket.send(json.dumps({
                "event": "start",
                "start": {
                    "hospital_id": hospital_id,
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4())
                }
            }))
            
            # Run receiver and sender loops concurrently
            await asyncio.gather(
                receive_messages(websocket),
                send_messages(websocket)
            )
    except Exception as e:
        print(f"{RED}Failed to connect to server at {uri}.{RESET}")
        print("Please verify that the FastAPI server is running.")
        print("Run command: uvicorn src.server:app --port 8000")

if __name__ == "__main__":
    try:
        asyncio.run(run_chat())
    except KeyboardInterrupt:
        print("\nGoodbye!")

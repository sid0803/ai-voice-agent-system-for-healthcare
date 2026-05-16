import asyncio
import json
import logging
import sys
from uuid import uuid4
import websockets

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("simulator")

async def simulate_with_uri(uri, hospital_id="apollo_metro"):
    
    print("\n" + "="*60)
    print(f"📞 PROJECT ASHA: CALL SIMULATOR (Hospital: {hospital_id})")
    print("="*60)
    print("Welcome! You can now talk to Asha. Type your message and hit Enter.")
    print("Note: The real system works via voice, but this simulator uses text for rapid testing.")
    print("-" * 60 + "\n")

    try:
        async with websockets.connect(uri) as websocket:
            # 1. Send the Exotel START event
            start_event = {
                "event": "start",
                "sequenceNumber": "1",
                "start": {
                    "accountSid": "ACxxxx",
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4()),
                    "from": "+919876543210",
                    "hospital_id": hospital_id
                },
                "streamSid": str(uuid4())
            }
            await websocket.send(json.dumps(start_event))
            logger.info(">>> [SYSTEM] Call Started. Asha is listening...")

            async def receive_messages():
                try:
                    async for message in websocket:
                        data = json.loads(message)
                        event = data.get("event")
                        
                        if event == "media":
                            # In real calls, this would be PCM. In simulator, we just acknowledge.
                            pass
                        elif event == "text": # Custom local hook for viewing AI output text
                            print(f"\n🤖 ASHA: {data.get('text')}")
                        elif event == "tool": # Custom local hook for viewing tool calls
                            print(f"🔧 [TOOL] {data.get('name')}({data.get('args')})")
                except websockets.ConnectionClosed:
                    print("\n❌ [SYSTEM] Connection closed by server.")

            async def send_messages():
                while True:
                    user_input = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
                    user_input = user_input.strip()
                    if not user_input:
                        continue
                    
                    if user_input.lower() in ["exit", "quit", "bye"]:
                        print("Hanging up...")
                        break
                    
                    # Send via our local Developer Hook
                    chat_msg = {
                        "type": "chat",
                        "text": user_input
                    }
                    await websocket.send(json.dumps(chat_msg))

            # Run both concurrently
            await asyncio.gather(receive_messages(), send_messages())

    except Exception as e:
        logger.error(f"Failed to connect to server: {e}")
        print("\n💡 Tip: Make sure the server is running (python -m src.server)")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Asha Voice Agent Simulator")
    parser.add_argument("--hospital", default="apollo_metro", help="Hospital ID")
    parser.add_argument("--port", type=int, default=8000, help="Port of the server")
    
    args = parser.parse_args()
    
    try:
        # Pass the port into the simulation
        async def run_sim():
            uri = f"ws://localhost:{args.port}/exotel-stream?hospital_id={args.hospital}&CallSid={uuid4()}&CallFrom=%2B919876543210"
            await simulate_with_uri(uri, args.hospital)
            
        # Refactor simulate_call to take URI
        asyncio.run(run_sim())
    except KeyboardInterrupt:
        pass

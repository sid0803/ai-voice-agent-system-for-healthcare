import asyncio
import json
import os
import time
import uuid
import base64
import websockets
from datetime import datetime

# Formatting for "Surgeon-Style" Demo Logs
BOLD = "\033[1m"
GREEN = "\033[92m"
BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

SCENARIOS_PATH = os.path.join(os.path.dirname(__file__), "demo_scenarios.json")

async def run_stage(websocket, stage_name, scenario):
    print(f"\n{BOLD}{BLUE}--- STARTING STAGE: {stage_name.upper()} ---{RESET}")
    print(f"{BLUE}[INFO]{RESET} Targeting Hospital ID: {scenario['hospital_id']}")
    
    # 1. Send the Exotel START event
    start_event = {
        "event": "start",
        "sequenceNumber": "1",
        "start": {
            "accountSid": "MOCK_SID",
            "callSid": f"demo_{uuid.uuid4().hex[:6]}",
            "streamSid": f"stream_{uuid.uuid4().hex[:6]}",
            "from": scenario["phone"],
            "hospital_id": scenario["hospital_id"]
        }
    }
    await websocket.send(json.dumps(start_event))
    print(f"{BLUE}[SYSTEM]{RESET} WebSocket Linked. Handshaking with Bedrock Nova...")

    for turn in scenario["turns"]:
        # Wait for AI response if not the first turn
        # In a real demo script, we listen for Asha's "event: text"
        
        print(f"\n{BOLD}{GREEN}👤 PATIENT:{RESET} {turn['input']} {BLUE}({turn['label']}){RESET}")
        
        # Send via Chat Backdoor
        chat_msg = {
            "type": "chat",
            "text": turn["input"]
        }
        await websocket.send(json.dumps(chat_msg))
        
        # Monitor for AI response
        ai_response_found = False
        timeout = time.time() + 10 # 10s wait for AI response
        
        while time.time() < timeout:
            try:
                raw_msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(raw_msg)
                
                if data.get("event") == "text":
                    print(f"{BOLD}{RED}🤖 ASHA:{RESET} {data.get('text')}")
                    ai_response_found = True
                    break
                elif data.get("event") == "tool":
                    print(f"{BLUE}[TOOL]{RESET} Invoking clinical tool: {data.get('name')}")
            except asyncio.TimeoutError:
                continue

        if not ai_response_found:
             print(f"{RED}[WARN]{RESET} AI response timed out. Proceeding to next turn...")

        # Dynamic Delay for Audience (2.5 - 3s)
        await asyncio.sleep(turn["delay"])

    print(f"\n{BOLD}{BLUE}--- STAGE COMPLETE: {stage_name.upper()} ---{RESET}")
    await asyncio.sleep(1)

async def main():
    print(f"{BOLD}{'='*60}")
    print(f"🏥 INDIISERVE NOVA SONIC: GOLDEN PATH AUTOMATION")
    print(f"{'='*60}{RESET}")
    
    if not os.path.exists(SCENARIOS_PATH):
        print(f"{RED}Error: {SCENARIOS_PATH} not found.{RESET}")
        return

    with open(SCENARIOS_PATH, "r") as f:
        scenarios = json.load(f)

    uri = "ws://localhost:8000/exotel-stream"
    
    try:
        # STAGE 1: Emergency
        async with websockets.connect(uri) as ws1:
            await run_stage(ws1, "Emergency Triage", scenarios["emergency_stage"])
        
        print(f"\n{BOLD}Note for Presenter:{RESET} Check your Dashboard now. Notice the {RED}CRITICAL ALERT{RESET} for this patient.")
        await asyncio.sleep(3)

        # STAGE 2: Booking
        async with websockets.connect(uri) as ws2:
            await run_stage(ws2, "Appointment Booking", scenarios["booking_stage"])
            
        print(f"\n{BOLD}Note for Presenter:{RESET} Notice the {GREEN}Successful Booking{RESET} metric increase on the Dashboard.")

    except Exception as e:
        print(f"\n{RED}Error: Could not connect to server at {uri}.{RESET}")
        print(f"Make sure the FastAPI server is running with DEMO_MODE=true.")
        print(f"Details: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDemo interrupted.")

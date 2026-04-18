import asyncio
import json
import logging
import os
import subprocess
import time
import websockets
import signal
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("e2e_verify")

async def run_e2e_test():
    print("\n" + "="*60)
    print("PROTOTYPE ASHA: END-TO-END AUTOMATED VERIFICATION")
    print("="*60 + "\n")

    # 1. Start Server in Background
    print("Step 1: Launching InDiiServe Asha Server...")
    # Use DEMO_MODE=true for text testing
    env = os.environ.copy()
    env["DEMO_MODE"] = "true"
    env["HOSPITAL_ID"] = "apollo_metro"
    env["PYTHONPATH"] = "."
    
    server_proc = subprocess.Popen(
        ["python", "-m", "src.server"],
        env=env,
        cwd=os.getcwd()
    )
    
    # 2. Polling for Health Check
    print("Step 2: Waiting for Health Check (http://localhost:8000/health)...")
    import httpx
    async with httpx.AsyncClient() as client:
        for i in range(15): # 15 attempts, 1s each
            try:
                resp = await client.get("http://localhost:8000/health", timeout=1.0)
                if resp.status_code == 200:
                    print("[OK] Server is Healthy.")
                    break
            except:
                pass
            print(f"Waiting... ({i+1}/15)")
            await asyncio.sleep(1)
        else:
            print("[FAIL] Server failed to start in time.")
            server_proc.terminate()
            return False

    from uuid import uuid4
    uri = f"ws://localhost:8000/exotel-stream?hospital_id=apollo_metro&CallSid={uuid4()}"

    test_results = {
        "connectivity": False,
        "hospital_info": False, 
        "emergency": False,
        "triage": False,
        "billing_sync": False,
        "ot_prediction": False
    }

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] Connected to Server WebSocket.")
            test_results["connectivity"] = True

            # 2. Test Case: Hospital Info
            print("\nTest Case 1: Checking Hospital Location...")
            await websocket.send(json.dumps({
                "type": "chat",
                "text": "Hello, can you tell me where you are located?"
            }))
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < 15:
                msg = await websocket.recv()
                data = json.loads(msg)
                print(f"[WS-RECV] {data}") # DEBUG
                if data.get("event") == "text":
                    print(f"AI Response: {data.get('text')}")
                    if "Apollo Metro" in data.get("text") or "located" in data.get("text").lower():
                        test_results["hospital_info"] = True
                        break
            
            if test_results["hospital_info"]:
                print("[OK] Test Case 1 passed.")
            else:
                print("[FAIL] Test Case 1 failed (Timeout/Incorrect Response).")

            # 3. Test Case: Emergency Detection
            print("\nTest Case 2: Simulating Emergency Distress...")
            await websocket.send(json.dumps({
                "type": "chat",
                "text": "I am having severe chest pain right now, please help!"
            }))

            start_time = time.time()
            while time.time() - start_time < 15:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data.get("event") == "tool" and data.get("name") == "handoffTool":
                    print("ðŸ”§ AI Triggered handoffTool correctly.")
                    test_results["emergency"] = True
                    break
                if data.get("event") == "text":
                    print(f"AI Response: {data.get('text')}")

            if test_results["emergency"]:
                print("[OK] Test Case 2 passed.")
            else:
                print("[FAIL] Test Case 2 failed.")

            # Test Case 3: Clinical Triage
            print("\n[TEST] Case 3: Clinical Triage")
            logger.info("[TEST] Injecting text input: I have a severe fever and my head hurts.")
            await websocket.send(json.dumps({
                "type": "chat",
                "text": "I have a severe fever and my head hurts."
            }))
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < 15:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data.get("event") == "tool" and data.get("name") == "clinicalTriageTool":
                    print("ðŸ”§ AI Triggered clinicalTriageTool correctly.")
                    test_results["triage"] = True
                    break
            
            if test_results["triage"]:
                print("[OK] Test Case 3 passed.")
            else:
                print("[FAIL] Test Case 3 failed.")

            # Test Case 4: Billing Inquiry
            print("\n[TEST] Case 4: Billing Inquiry")
            logger.info("[TEST] Injecting text input: How much is my current bill?")
            await websocket.send(json.dumps({"type": "chat", "text": "How much is my current bill?"}))
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < 15:
                msg = await websocket.recv()
                data = json.loads(msg)
                print(f"[WS-RECV] {data}")
                if data.get("event") == "text":
                    print(f"AI Response: {data.get('text')}")
                    if "billing" in data.get("text").lower() or "amount" in data.get("text").lower() or "pay.indiiserve" in data.get("text").lower():
                        test_results["billing_sync"] = True
                        break
            
            # Test Case 5: OT Scheduling Prediction
            print("\n[TEST] Case 5: OT Scheduling Prediction")
            logger.info("[TEST] Injecting text input: How long will an angioplasty surgery take?")
            await websocket.send(json.dumps({"type": "chat", "text": "How long will an angioplasty surgery take?"}))
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < 15:
                msg = await websocket.recv()
                data = json.loads(msg)
                print(f"[WS-RECV] {data}")
                if data.get("event") == "text":
                    print(f"AI Response: {data.get('text')}")
                    if "OT block" in data.get("text") or "minutes" in data.get("text").lower() or "slot" in data.get("text").lower():
                        test_results["ot_prediction"] = True
                        break

    except Exception as e:
        print(f"[FAIL] Connection Error: {e}")
    finally:
        print("\nStep 5: Cleaning up resources...")
        server_proc.terminate()
        server_proc.wait()
        
    print("\n" + "="*50)
    print("         E2E VERIFICATION REPORT")
    print("="*50)
    for test, passed in test_results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{test:<20} : {status}")
    print("="*50 + "\n")

    if all(test_results.values()):
        print("VERIFICATION COMPLETE: Project Asha is 100% stable and demo-ready.")
        return True
    else:
        print("VERIFICATION INCOMPLETE: Some tests failed. Check Bedrock Connectivity.")
        return False

if __name__ == "__main__":
    asyncio.run(run_e2e_test())

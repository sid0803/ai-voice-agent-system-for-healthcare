import asyncio
import json
import logging
import time
import random

logger = logging.getLogger(__name__)

class MockS2SStream:
    """Simulates the Bedrock Bidirectional Stream for offline clinical testing.
    
    Requirement: 100% Reliable Sandbox (Prove plumbing works without cloud).
    """
    
    def __init__(self, session_id, client):
        self.session_id = session_id
        self.client = client
        self.queue = asyncio.Queue()
        self._is_active = True

    async def send_event(self, event_json: str):
        """Handle incoming events (Audio/Text/ToolResults) from the server."""
        try:
            data = json.loads(event_json)
            event = data.get("event", {})
            
            # Extract text from either message or textInput structure
            text = (event.get("message", {}).get("text", "") or 
                    event.get("textInput", {}).get("content", "")).lower()
            
            if text:
                logger.info(f"[MOCK] Queueing text: {text}")
                await self.queue.put(text)
            
            # Handle Tool Results
            tool_result = event.get("toolResult")
            if tool_result:
                logger.info(f"[MOCK] Received Tool Result: {tool_result.get('toolUseId')}")
                await self._simulate_text("I've found the information for you. Is there anything else you need?")
                
        except Exception as e:
            logger.error(f"[MOCK] Failed to parse event: {e}")

    async def start_processing(self):
        """Background loop that simulates AI 'Thinking' and 'Responding'."""
        self._task = asyncio.create_task(self._process_loop())

    async def _process_loop(self):
        while self._is_active:
            try:
                patient_text = await self.queue.get()
                
                # Simulate thinking delay
                await asyncio.sleep(0.1)
                
                # --- CLINICAL LOGIC SIMULATOR (Keywords to Tools) ---
                
                # 1. Hospital Info
                if any(k in patient_text for k in ["where", "address", "location", "pharmacy"]):
                    await self._simulate_text("Indiiserve Healthcare is located at Sector 5, Cyber City. Is there anything else you need?")
                    await self._simulate_tool_call(
                        "hospitalInfoTool", 
                        {"query": patient_text}
                    )
                
                # 2. Emergency Trigger
                elif any(k in patient_text for k in ["emergency", "ambulance", "accident", "chest pain", "bleeding"]):
                    await self._simulate_text("This sounds urgent. Please stay on the line, I am connecting you to our emergency desk immediately.")
                    await self._simulate_tool_call(
                        "handoffTool", 
                        {"reason": "Emergency distress detected."}
                    )

                # 3. Triage / Symptom Check
                elif any(k in patient_text for k in ["pain", "fever", "cough", "hurts"]):
                    await self._simulate_tool_call(
                        "clinicalTriageTool",
                        {
                            "symptoms": patient_text,
                            "pain_intensity": 7 if "severe" in patient_text else 4,
                            "onset_duration": "since today",
                            "decision_reason": "Patient mentioned clinical symptoms."
                        }
                    )

                # 4. Billing Inquiry
                elif any(k in patient_text for k in ["billing", "bill", "payment", "how much", "cost"]):
                    await self._simulate_text("Let me check your current billing breakdown for you.")
                    await self._simulate_tool_call(
                        "getBillingInfoTool",
                        {"patient_name": "Patient", "query": patient_text}
                    )
                
                # 5. OT / Surgery consultation
                elif any(k in patient_text for k in ["surgery", "operation", "procedure", " ot "]):
                    await self._simulate_text("Operation Theatre and surgical scheduling require direct clinical evaluation by our surgeons. Please call our hospital desk at 8 0 4 0 0 0 9 0 0 0.")

                # 6. Default Greeting / Info
                else:
                    await self._simulate_text("Hello, this is Asha at Indiiserve Healthcare. How can I help you?")
                
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MOCK] Error in processing loop: {e}")
                await asyncio.sleep(1)

    async def _simulate_text(self, text: str):
        """Dispatch a text response back to the client."""
        logger.info(f"[MOCK] Dispatching Text Output: {text[:40]}...")
        self.client._dispatch_event(self.session_id, "textOutput", {
            "role": "ASSISTANT",
            "content": text
        })
        # Simulate audio output so Exotel stream gets media (even if empty PCM)
        import base64
        self.client._dispatch_event(self.session_id, "audioOutput", {
            "content": base64.b64encode(b'\x00' * 8000).decode("utf-8")
        })

    async def _simulate_tool_call(self, tool_name: str, args: dict):
        """Dispatch a tool invocation back to the client."""
        call_id = f"mock-call-{random.randint(1000, 9999)}"
        self.client._dispatch_event(self.session_id, "toolUse", {
            "toolUseId": call_id,
            "name": tool_name,
            "content": json.dumps(args)
        })

    def close(self):
        self._is_active = False
        if hasattr(self, "_task") and self._task and not self._task.done():
            self._task.cancel()

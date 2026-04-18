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
        asyncio.create_task(self._process_loop())

    async def _process_loop(self):
        while self._is_active:
            try:
                patient_text = await self.queue.get()
                
                # Simulate thinking delay
                await asyncio.sleep(0.1)
                
                # --- CLINICAL LOGIC SIMULATOR (Keywords to Tools) ---
                
                # 1. Hospital Info
                if any(k in patient_text for k in ["where", "address", "location", "pharmacy"]):
                    await self._simulate_text("Apollo Metro is located at the city center. Is there anything else you need?")
                    await self._simulate_tool_call(
                        "hospitalInfoTool", 
                        {"query": patient_text}
                    )
                
                # 2. Emergency Trigger
                elif any(k in patient_text for k in ["emergency", "ambulance", "accident", "chest pain", "bleeding"]):
                    # AI first acknowledges distress
                    await self._simulate_text("I understand this is an emergency. Please stay calm, I am connecting you to our emergency desk right now.")
                    await self._simulate_tool_call(
                        "handoffTool", 
                        {"reason": "Emergency distress detected by AI."}
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
                    await self._simulate_text("Let me check your current billing status and the breakdown for you.")
                    await self._simulate_tool_call(
                        "getBillingInfoTool",
                        {"patient_name": "Test Patient", "query": patient_text}
                    )
                
                # 5. OT Scheduling / Surgery prediction
                elif any(k in patient_text for k in ["surgery", "operation", "angioplasty", "procedure"]):
                    await self._simulate_text("I can predict the total OT block time for that procedure based on our clinical data.")
                    await self._simulate_tool_call(
                        "predictOTScheduleTool",
                        {"procedure_name": "Angioplasty", "doctor_name": "Dr. Sameer Kulkarni"}
                    )

                # 6. Default Greeting / Info
                else:
                    await self._simulate_text("Hello, this is Asha from Apollo Metro. How can I help you today?")
                
                self.queue.task_done()
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

    async def _simulate_tool_call(self, tool_name: str, args: dict):
        """Dispatch a tool invocation back to the client."""
        call_id = f"mock-call-{random.randint(1000, 9999)}"
        self.client._dispatch_event(self.session_id, "toolUse", {
            "toolUseId": call_id,
            "name": tool_name,
            "input": args
        })

    def close(self):
        self._is_active = False

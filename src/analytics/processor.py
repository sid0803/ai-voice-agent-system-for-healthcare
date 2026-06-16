import json
import logging
import os
import boto3
import re
from src.analytics.dynamodb_client import dynamodb_analytics

logger = logging.getLogger(__name__)

class AnalyticsProcessor:
    """The AI 'Data Scientist' - extracts structured metrics from call transcripts."""
    
    def __init__(self):
        from botocore.config import Config as _BotoConfig
        
        # [MED FIX] Ensure connection pooling and tcp_keepalive (OPT-07 parity)
        _BOTO_POOL_CONFIG = _BotoConfig(
            max_pool_connections=10,
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
            tcp_keepalive=True,
        )
        client = boto3.client(
            service_name="bedrock-runtime", 
            region_name=os.environ.get("BEDROCK_REGION", "us-east-1"),
            config=_BOTO_POOL_CONFIG
        )
        self.bedrock_runtime = client
        # Use a cost-effective model for post-call analysis
        # us.amazon.nova-lite-v1:0 replaces the deprecated Titan Text Express v1
        self.model_id = os.environ.get("ANALYTICS_MODEL_ID", "us.amazon.nova-lite-v1:0")

    async def process_call(self, session_id: str, phone: str, hospital_id: str, transcript: list, duration: int, token_usage: dict = None):
        """Analyze the transcript using AI and save results to DynamoDB."""
        import asyncio
        if not transcript:
            return

        formatted_transcript = "\n".join([f"{m['role']}: {m['content']}" for m in transcript])
        
        prompt = f"""
        System: You are an expert healthcare data scientist. Analyze the following call transcript from 'Asha', a hospital AI receptionist.
        Extract the following fields in JSON format:
        1. sentiment: (Positive, Neutral, Worried, Angry)
        2. intent: (Appointment Booking, Doctor Inquiry, Report Status, Hospital Visit, Billing Inquiry, OT Scheduling, Other)
        3. department: (Cardiology, Pediatrics, General, Orthopedics, etc.)
        4. outcome: (booked, inquiry, abandoned)
        5. summary: A 1-sentence summary of the patient's need.
        6. successful_booking: (true/false)
        7. urgency_score: (INT 1-5, where 5 is critical emergency)
        8. is_emergency: (true/false based on clinical severity)
        9. symptoms_list: (comma-separated list of symptoms mentioned)
        10. follow_up_priority: (Low, Med, High)

        Transcript:
        {formatted_transcript}

        JSON Response:
        """

        try:
            # Wrap blocking sync call in a thread
            response = await asyncio.to_thread(
                self.bedrock_runtime.invoke_model,
                modelId=self.model_id,
                body=json.dumps({
                    "messages": [
                        {"role": "user", "content": [{"text": prompt}]}
                    ],
                    "inferenceConfig": {
                        "maxTokens": 512,
                        "temperature": 0.1,
                        "topP": 0.9
                    }
                })
            )
            result = json.loads(response["body"].read())
            # Nova response format: output.message.content[0].text
            output_text = (
                result.get("output", {})
                      .get("message", {})
                      .get("content", [{}])[0]
                      .get("text", "{}")
            )

            # Extract JSON from output text (handle conversational preamble)
            try:
                # Robust extraction using regex to find the first '{' and last '}'
                match = re.search(r'\{.*\}', output_text, re.DOTALL)
                if match:
                    analytics = json.loads(match.group(0))
                else:
                    logger.warning(f"No JSON found in AI response for {session_id}")
                    analytics = {}
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from AI for {session_id}")
                analytics = {}

            # Save to DynamoDB in a non-blocking thread
            await asyncio.to_thread(
                self._save_to_dynamodb, session_id, phone, hospital_id, analytics, duration, token_usage or {}
            )
            logger.info(f"[ANALYTICS] Processed call {session_id[:8]} - Outcome: {analytics.get('outcome')} | Tokens: {(token_usage or {}).get('total_tokens', 0)}")

        except Exception:
            logger.exception(f"Failed to process analytics for session {session_id}")

    def _save_to_dynamodb(self, session_id, phone, hospital_id, analytics, duration, token_usage=None):
        """Insert processed metrics into DynamoDB."""
        dynamodb_analytics.save_analytics(session_id, phone, hospital_id, analytics, duration, token_usage or {})

# Global instance
analytics_processor = AnalyticsProcessor()

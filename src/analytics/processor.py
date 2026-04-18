import json
import logging
import os
import boto3
import re
from src.analytics.rds_client import rds_analytics

logger = logging.getLogger(__name__)

class AnalyticsProcessor:
    """The AI 'Data Scientist' - extracts structured metrics from call transcripts."""
    
    def __init__(self):
        self.bedrock_runtime = boto3.client(
            service_name="bedrock-runtime", 
            region_name=os.environ.get("BEDROCK_REGION", "us-east-1")
        )
        # Use a cost-effective model for post-call analysis
        # amazon.nova-lite-v1:0 replaces the deprecated Titan Text Express v1
        self.model_id = os.environ.get("ANALYTICS_MODEL_ID", "amazon.nova-lite-v1:0")

    async def process_call(self, session_id: str, phone: str, hospital_id: str, transcript: list, duration: int):
        """Analyze the transcript using AI and save results to RDS."""
        import asyncio
        if not transcript:
            return

        formatted_transcript = "\n".join([f"{m['role']}: {m['content']}" for m in transcript])
        
        prompt = f"""
        System: You are an expert healthcare data scientist. Analyze the following call transcript from 'Asha', a hospital AI receptionist.
        Extract the following fields in JSON format:
        1. sentiment: (Positive, Neutral, Worried, Angry)
        2. intent: (Appointment Booking, Doctor Inquiry, Report Status, Hospital Visit, Other)
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

            # Save to RDS in a non-blocking thread
            await asyncio.to_thread(
                self._save_to_rds, session_id, phone, hospital_id, analytics, duration
            )
            logger.info(f"[ANALYTICS] Processed call {session_id[:8]} - Outcome: {analytics.get('outcome')}")

        except Exception:
            logger.exception(f"Failed to process analytics for session {session_id}")

    def _save_to_rds(self, session_id, phone, hospital_id, analytics, duration):
        """Insert processed metrics into Postgres."""
        conn = rds_analytics.get_connection()
        if not conn:
            return

        try:
            # Encrypt PII before saving to analytics (Requirement P1 Hardening)
            encrypted_phone = rds_analytics.encrypt_data(phone)
            
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO hospital_analytics 
                    (session_id, phone_number, hospital_id, sentiment, intent, department, outcome, duration_seconds, transcript_summary, is_successful_booking, urgency_score, is_emergency, symptoms_list, follow_up_priority)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO NOTHING;
                """, (
                    session_id,
                    encrypted_phone,
                    hospital_id,
                    analytics.get("sentiment", "Neutral"),
                    analytics.get("intent", "General"),
                    analytics.get("department", "General"),
                    analytics.get("outcome", "inquiry"),
                    duration,
                    analytics.get("summary", ""),
                    analytics.get("successful_booking", False),
                    analytics.get("urgency_score", 1),
                    analytics.get("is_emergency", False),
                    analytics.get("symptoms_list", ""),
                    analytics.get("follow_up_priority", "Low")
                ))
                conn.commit()
        except Exception:
            logger.exception("Failed to insert analytics row")
        finally:
            conn.close()

# Global instance
analytics_processor = AnalyticsProcessor()

import json
import logging
import os
import pathlib
import boto3

logger = logging.getLogger(__name__)

class KnowledgeDistiller:
    """The 'Brain' extension: Learns from user conversations and system events."""
    
    def __init__(self):
        from botocore.config import Config
        boto_config = Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0})
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("BEDROCK_REGION", "us-east-1"), config=boto_config)
        self.dynamo = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "ap-south-1"), config=boto_config)
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "InDiiServe_Call_Transcript_1")
        
        self.knowledge_file = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "unified_hospital_kb.json"

    def _get_recent_transcripts(self, limit=10):
        """Fetch the latest call transcripts from DynamoDB."""
        try:
            table = self.dynamo.Table(self.table_name)
            # Simplified scan for the demo/pilot (ideally should be a GSI on timestamp)
            response = table.scan(Limit=limit)
            return response.get("Items", [])
        except Exception as e:
            logger.error(f"[LEARNING] Failed to fetch transcripts: {e}")
            return []

    def distill_knowledge_from_transcript(self, transcript_item):
        """Use Bedrock to identify 'Learning Moments' in a conversation."""
        transcript_text = ""
        for msg in transcript_item.get("transcript", []):
            role = msg.get("role", "UNKNOWN")
            content = msg.get("content", "")
            transcript_text += f"{role}: {content}\n"

        prompt = f"""
        Analyze the following call transcript between a Hospital AI Assistant (Asha) and a Patient.
        Your goal is to extract 'New Facts' or 'Knowledge Corrections' that the AI should LEARN for future calls.
        
        Example Learing Moments:
        - Patient says: "No, Dr. Sen's clinic is on the 3rd floor now." (The AI should learn the new floor).
        - AI says: "I don't know the parking rates." Patient says: "It's 20 rupees for an hour." (AI should learn parking rate).
        
        Transcript:
        {transcript_text}
        
        Output only a JSON array of objects with "question" and "answer" keys.
        
        ## CRITICAL SCOPE RULE:
        - ONLY extract facts related to InDiiServe Healthcare, Doctors, Appointments, Hospital floors, or medical operations.
        - COMPLETELY IGNORE and DISCARD any information about travel, trips, hotels, or tourism.
        - If no healthcare-specific knowledge is found, return an empty array [].
        - Do NOT include PII like names or phone numbers.
        """

        try:
            body = json.dumps({
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
                "inferenceConfig": {"maxTokens": 500}
            })
            
            # Use Nova Lite instead of deprecated Claude models
            model_id = os.getenv(
                "DISTILLER_MODEL_ID",
                "us.amazon.nova-lite-v1:0"
            )
            response = self.bedrock.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=body
            )
            
            result = json.loads(response["body"].read())
            output_text = (
                result.get("output", {})
                      .get("message", {})
                      .get("content", [{}])[0]
                      .get("text", "[]")
            )
            
            import re
            match = re.search(r'\[.*\]', output_text, re.DOTALL)
            if match:
                extracted = json.loads(match.group(0))
            else:
                extracted = []
                
            return extracted
        except Exception as e:
            logger.error(f"[LEARNING] Distillation failed: {e}")
            return []

    def run_learning_cycle(self):
        """Main loop for the learning worker."""
        logger.info("[LEARNING] Starting knowledge distillation cycle...")
        items = self._get_recent_transcripts()
        
        all_new_facts = []
        for item in items:
            facts = self.distill_knowledge_from_transcript(item)
            if facts:
                all_new_facts.extend(facts)
        
        if all_new_facts:
            self._save_knowledge(all_new_facts)
            logger.info(f"[LEARNING] Discovered {len(all_new_facts)} new facts from recent calls.")
            return True
        return False

    def _save_knowledge(self, new_facts):
        if not self.knowledge_file.exists():
            logger.error(f"[LEARNING] KB file does not exist at {self.knowledge_file}")
            return
            
        try:
            with open(self.knowledge_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
        except Exception as e:
            logger.error(f"[LEARNING] Failed to read unified KB file: {e}")
            return
            
        if "faq" not in kb_data:
            kb_data["faq"] = []
            
        import time
        import random
        from datetime import datetime, timezone
        
        added_count = 0
        for fact in new_facts:
            q = fact.get("question", "")
            a = fact.get("answer", "")
            if isinstance(q, dict):
                q = q.get("text", str(q))
            elif not isinstance(q, str):
                q = str(q) if q is not None else ""
            if isinstance(a, dict):
                a = a.get("text", str(a))
            elif not isinstance(a, str):
                a = str(a) if a is not None else ""
            q = q.strip()
            a = a.strip()
            if not q or not a:
                continue
                
            # Check for duplicate in existing question_variants
            duplicate = False
            for entry in kb_data["faq"]:
                variants = [v.lower().strip() for v in entry.get("question_variants", [])]
                if q.lower().strip() in variants:
                    duplicate = True
                    break
                    
            if not duplicate:
                timestamp = int(time.time())
                rand_id = random.randint(1000, 9999)
                new_faq = {
                    "id": f"faq_learned_{timestamp}_{rand_id}",
                    "category": "Learned Fact",
                    "intent": f"learned_fact_{timestamp}_{rand_id}",
                    "question_variants": [q],
                    "answer": a,
                    "tags": ["learned"]
                }
                kb_data["faq"].append(new_faq)
                added_count += 1
                
        if added_count > 0:
            if "metadata" in kb_data:
                kb_data["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
                
            try:
                with open(self.knowledge_file, "w", encoding="utf-8") as f:
                    json.dump(kb_data, f, indent=2, ensure_ascii=False)
                logger.info(f"[LEARNING] Appended {added_count} learned facts directly to {self.knowledge_file}")
            except Exception as e:
                logger.error(f"[LEARNING] Failed to write updated unified KB file: {e}")

# Global Instance
learning_distiller = KnowledgeDistiller()

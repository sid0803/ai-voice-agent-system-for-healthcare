import json
import logging
import os
import pathlib
import re
import time
import random
import tempfile
from datetime import datetime, timezone
import boto3

logger = logging.getLogger(__name__)

class KnowledgeDistiller:
    """The 'Brain' extension: Extracts candidate facts from calls for human review."""
    
    def __init__(self):
        from botocore.config import Config
        boto_config = Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"}
        )
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("BEDROCK_REGION", "us-east-1"), config=boto_config)
        self.dynamo = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "ap-south-1"), config=boto_config)
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "InDiiServe_Call_Transcript_1")
        
        self.knowledge_file = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "unified_hospital_kb.json"
        self.pending_file = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "pending_facts.json"
        self._last_processed_session_ids: set[str] = set()

    def _get_recent_transcripts(self, limit=10):
        """Fetch the latest call transcripts from DynamoDB."""
        try:
            table = self.dynamo.Table(self.table_name)
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

        # SEC-3: Truncate transcript text to prevent huge payloads or prompt injection
        transcript_text = transcript_text[:2000]

        prompt = f"""
        Analyze the following call transcript between a Hospital AI Assistant (Asha) and a Patient.
        Your goal is to extract 'New Facts' or 'Knowledge Corrections' that the AI should LEARN for future calls.
        
        Example Learning Moments:
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
            
            match = re.search(r'\[.*\]', output_text, re.DOTALL)
            if match:
                extracted = json.loads(match.group(0))
            else:
                extracted = []
            
            # SEC-3: Validate each extracted fact
            valid_facts = []
            for fact in extracted:
                q = str(fact.get("question", "")).strip()
                a = str(fact.get("answer", "")).strip()
                if not q or not a or len(q) > 300 or len(a) > 500:
                    continue
                # Reject suspicious URLs, scripts, or non-medical instructions
                if re.search(r"(http://|https://|<script|javascript:|eval\()", a, re.IGNORECASE):
                    continue
                valid_facts.append({"question": q, "answer": a})
                
            return valid_facts
        except Exception as e:
            logger.error(f"[LEARNING] Distillation failed: {e}")
            return []

    def run_learning_cycle(self):
        """Main loop for the learning worker."""
        logger.info("[LEARNING] Starting knowledge distillation cycle...")
        items = self._get_recent_transcripts()
        if not items:
            logger.info("[LEARNING] No transcripts returned from store.")
            return False

        # COST-1: Check if any items are new to avoid calling LLM on identical transcripts
        current_session_ids = {item.get("session_id", "") for item in items if item.get("session_id")}
        new_items = [item for item in items if item.get("session_id") not in self._last_processed_session_ids]
        
        if not new_items and self._last_processed_session_ids:
            logger.info("[LEARNING] No new transcripts since last cycle. Skipping LLM invocation.")
            return False

        all_new_facts = []
        for item in new_items or items:
            facts = self.distill_knowledge_from_transcript(item)
            if facts:
                all_new_facts.extend(facts)
        
        self._last_processed_session_ids = current_session_ids

        if all_new_facts:
            added = self._save_pending_knowledge(all_new_facts)
            logger.info(f"[LEARNING] Discovered {len(all_new_facts)} candidates ({added} queued for review).")
            return True
        return False

    def _save_pending_knowledge(self, new_facts: list[dict]) -> int:
        """HIGH-6: Save discovered facts to pending review queue instead of modifying production KB."""
        pending_list = self.get_pending_facts()
        
        added_count = 0
        for fact in new_facts:
            q = fact.get("question", "").strip()
            a = fact.get("answer", "").strip()
            if not q or not a:
                continue
                
            # Avoid duplicate questions in pending queue
            if any(p.get("question", "").lower().strip() == q.lower().strip() for p in pending_list):
                continue
                
            timestamp = int(time.time())
            rand_id = random.randint(1000, 9999)
            fact_entry = {
                "fact_id": f"fact_{timestamp}_{rand_id}",
                "question": q,
                "answer": a,
                "status": "PENDING_REVIEW",
                "submitted_at": datetime.now(timezone.utc).isoformat()
            }
            pending_list.append(fact_entry)
            added_count += 1

        if added_count > 0:
            self._write_pending_file(pending_list)
            logger.info(f"[LEARNING] Queued {added_count} new candidate facts for human review.")
        return added_count

    def get_pending_facts(self) -> list[dict]:
        """Fetch all facts waiting for human/admin review."""
        if not self.pending_file.exists():
            return []
        try:
            with open(self.pending_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[LEARNING] Failed to read pending facts file: {e}")
            return []

    def _write_pending_file(self, data: list[dict]):
        """Atomic write to pending facts file."""
        self.pending_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.pending_file.parent,
                suffix='.tmp',
                delete=False,
                encoding='utf-8'
            ) as tmp:
                json.dump(data, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            os.replace(tmp_path, self.pending_file)
        except Exception as e:
            logger.error(f"[LEARNING] Failed to write pending facts: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def approve_pending_fact(self, fact_id: str) -> bool:
        """Approve a fact from pending queue and commit it to production KB."""
        pending_list = self.get_pending_facts()
        target_fact = None
        remaining = []
        for f in pending_list:
            if f.get("fact_id") == fact_id:
                target_fact = f
            else:
                remaining.append(f)

        if not target_fact:
            logger.warning(f"[LEARNING] Fact {fact_id} not found in pending review queue.")
            return False

        # Append to production KB
        try:
            if not self.knowledge_file.exists():
                logger.error(f"[LEARNING] KB file does not exist at {self.knowledge_file}")
                return False
                
            with open(self.knowledge_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
                
            if "faq" not in kb_data:
                kb_data["faq"] = []

            timestamp = int(time.time())
            rand_id = random.randint(1000, 9999)
            new_faq = {
                "id": f"faq_approved_{timestamp}_{rand_id}",
                "category": "Approved Fact",
                "intent": f"approved_fact_{timestamp}_{rand_id}",
                "question_variants": [target_fact["question"]],
                "answer": target_fact["answer"],
                "tags": ["reviewed", "approved"]
            }
            kb_data["faq"].append(new_faq)

            if "metadata" in kb_data:
                kb_data["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Atomic write to production KB
            tmp_path = None
            with tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.knowledge_file.parent,
                suffix='.tmp',
                delete=False,
                encoding='utf-8'
            ) as tmp:
                json.dump(kb_data, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            os.replace(tmp_path, self.knowledge_file)

            # Update pending file
            self._write_pending_file(remaining)
            logger.info(f"[LEARNING] Approved and committed fact {fact_id} to {self.knowledge_file}")
            return True
        except Exception as e:
            logger.error(f"[LEARNING] Error approving fact {fact_id}: {e}")
            return False

    def reject_pending_fact(self, fact_id: str) -> bool:
        """Reject and remove a fact from the pending review queue."""
        pending_list = self.get_pending_facts()
        new_list = [f for f in pending_list if f.get("fact_id") != fact_id]
        if len(new_list) == len(pending_list):
            return False
        self._write_pending_file(new_list)
        logger.info(f"[LEARNING] Rejected fact {fact_id} from pending review queue.")
        return True

# Global Instance
learning_distiller = KnowledgeDistiller()

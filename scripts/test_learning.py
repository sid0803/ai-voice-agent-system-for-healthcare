import sys
import os
import json
import logging
import asyncio
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.learning.distiller import learning_distiller
from src.tools import sync_community_knowledge, hospital_info

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

async def test_learning_cycle():
    print("\n" + "="*50)
    print("🚀 PROJECT ASHA: AUTOMATIC LEARNING TEST")
    print("="*50 + "\n")

    # 1. Mock a transcript where the user provides new information
    # In a real scenario, this would come from DynamoDB
    mock_transcript = {
        "session_id": "test-session-123",
        "transcript": [
            {"role": "ASSISTANT", "content": "Welcome to InDiiServe! How can I help you?"},
            {"role": "USER", "content": "I want to complain about parking. You said it's 20 rupees, but the guy at the gate said the **new rate is 50 rupees per day** since this morning."},
            {"role": "ASSISTANT", "content": "I'm sorry for the confusion. I'll make a note of this new parking rate."}
        ]
    }

    print("Step 1: Simulating Knowledge Extraction from transcript...")
    facts = learning_distiller.distill_knowledge_from_transcript(mock_transcript)
    
    if not facts:
        print("❌ No knowledge extracted. Check Bedrock connectivity.")
        return

    print(f"✅ Extracted Fact: {facts[0]}")
    
    # 2. Save fact manually (to simulate DynamoDB flow)
    learning_distiller._save_knowledge(facts)
    print("Step 2: Knowledge persisted to distilled_facts.json")

    # 3. Trigger FAISS Re-indexing
    print("Step 3: Syncing distilled facts into the Vector Brain...")
    sync_community_knowledge()
    
    # 4. Verify the AI now 'Knows' the fact
    print("\n" + "-"*30)
    print("Step 4: Querying the system for the learned fact...")
    print("-"*30)
    
    query_args = {"query": "What are the parking charges?"}
    result = hospital_info(query_args, hospital_id="apollo_metro")
    
    print(f"AI RESPONSE: {result['answer']}")
    
    if "50" in result['answer']:
        print("\n🏆 SUCCESS: Project Asha has successfully learned the new parking rate!")
    else:
        print("\n❌ FAILURE: AI did not retrieve the updated information.")

if __name__ == "__main__":
    asyncio.run(test_learning_cycle())

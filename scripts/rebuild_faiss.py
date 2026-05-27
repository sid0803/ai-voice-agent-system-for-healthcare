#!/usr/bin/env python3
"""
FAISS Rebuild Script for InDiiServe Voice Agent
Clears the stale FAISS index and re-seeds it from the new distilled_facts.json
Run: ./linux_venv/bin/python rebuild_faiss.py
"""

import json
import pathlib
import sys
import os
import numpy as np

# Add project root to path
sys.path.insert(0, '/home/ubuntu/indiiserve')

# --- Paths ---
BASE = pathlib.Path('/home/ubuntu/indiiserve')
CACHE_DIR = BASE / 'cache'
FAISS_INDEX = CACHE_DIR / 'kb_faiss.index'
FAISS_META  = CACHE_DIR / 'kb_faiss_meta.json'
FACTS_FILE  = BASE / 'data' / 'knowledge' / 'distilled_facts.json'

print("=" * 60)
print("  InDiiServe FAISS Knowledge Brain Rebuild")
print("=" * 60)

# Step 1 — Count current facts
with open(FACTS_FILE, 'r') as f:
    facts = json.load(f)
print(f"[1] Facts loaded from distilled_facts.json: {len(facts)}")

# Step 2 — Delete old stale index
if FAISS_INDEX.exists():
    FAISS_INDEX.unlink()
    print(f"[2] Deleted stale FAISS index ({FAISS_INDEX})")
if FAISS_META.exists():
    FAISS_META.unlink()
    print(f"[2] Deleted stale FAISS meta ({FAISS_META})")

# Step 3 — Load AWS credentials from .env
env_file = BASE / '.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())
    print("[3] .env loaded")

# Step 4 — Import FAISS and Bedrock client
try:
    import faiss
    import boto3
    from botocore.config import Config
    print(f"[4] FAISS loaded successfully")
except ImportError as e:
    print(f"[ERROR] Cannot import required modules: {e}")
    sys.exit(1)

EMBED_DIM = 1024
SIMILARITY_THRESHOLD = 0.85

boto_cfg = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2})
embed_client = boto3.client(
    'bedrock-runtime',
    region_name=os.environ.get('BEDROCK_REGION', 'us-east-1'),
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    config=boto_cfg,
)

# Step 5 — Create fresh index
index = faiss.IndexFlatIP(EMBED_DIM)
meta = []

def embed(text):
    resp = embed_client.invoke_model(
        modelId='amazon.titan-embed-text-v2:0',
        contentType='application/json',
        accept='application/json',
        body=json.dumps({'inputText': text}),
    )
    result = json.loads(resp['body'].read())
    vec = np.array(result['embedding'], dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec

# Step 6 — Embed and index each fact
print(f"[5] Embedding {len(facts)} facts into FAISS (this takes ~2-3 mins)...")
success = 0
failed = 0
for i, item in enumerate(facts):
    q = item.get('question', '').strip()
    a = item.get('answer', '').strip()
    if not q or not a:
        continue
    try:
        vec = embed(q)
        index.add(vec.reshape(1, -1))
        meta.append({'query': q, 'answer': a, 'timestamp': 0.0})
        success += 1
        if (i + 1) % 20 == 0:
            print(f"    Progress: {i+1}/{len(facts)} embedded...")
    except Exception as e:
        print(f"    [WARN] Failed to embed item {i}: {e}")
        failed += 1

# Step 7 — Save to disk
CACHE_DIR.mkdir(exist_ok=True)
faiss.write_index(index, str(FAISS_INDEX))
with open(FAISS_META, 'w') as f:
    json.dump(meta, f)

print()
print("=" * 60)
print(f"  DONE! Indexed: {success} facts | Failed: {failed}")
print(f"  FAISS total entries: {index.ntotal}")
print(f"  Index saved to: {FAISS_INDEX}")
print("=" * 60)

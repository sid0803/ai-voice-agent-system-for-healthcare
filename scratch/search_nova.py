with open("src/nova_client.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        if "_process_response_stream" in line or "stream_audio_chunk" in line or "max_chunks_per_batch" in line:
            print(f"Line {i}: {line.strip()}")

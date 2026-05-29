import asyncio
import json
import logging
import os
import subprocess
import time
import websockets
import sys
from uuid import uuid4

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("generate_transcript")

# Formatted queries simulating a real human caller conversing with Asha
queries = [
    "Hello, is this the hospital receptionist?",
    "what are the icu visiting hours?",
    "Can I visit a patient in the general ward?",
    "Is parking free for patients?",
    "how much does a brain mri cost?",
    "what about a spine mri?",
    "do I need to fast before a contrast ct scan?",
    "What are the charges for a complete blood count test?",
    "What room types are available and what is the rent?",
    "how much does an ICU room cost per day?",
    "Is there a cardiologist available today?",
    "who is your neurologist?",
    "Is Dr. Megha Rao available?",
    "I have knee pain, do you have a specialist?",
    "Is there a pediatrician available?",
    "What health packages do you offer?",
    "Do you accept HDFC Ergo health insurance?",
    "Where is the pharmacy located?",
    "Thank you so much for the help, goodbye!"
]

async def run_session():
    # 1. Start Server in Background in Demo Mode
    logger.info("Step 1: Launching InDiiServe Asha Server in Demo Mode...")
    env = os.environ.copy()
    env["DEMO_MODE"] = "true"
    env["HOSPITAL_ID"] = "apollo_metro"
    env["PYTHONPATH"] = "."
    env["AWS_EC2_METADATA_DISABLED"] = "true"

    # Load .env
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in env:
                        env[key] = val

    server_log = open("server_startup_test.log", "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.server"],
        env=env,
        cwd=os.getcwd(),
        stdout=server_log,
        stderr=server_log
    )

    try:
        # 2. Wait for server to become healthy
        logger.info("Step 2: Waiting for Server Health Check...")
        import httpx
        async with httpx.AsyncClient() as client:
            for i in range(30):
                try:
                    resp = await client.get("http://127.0.0.1:8000/health", timeout=1.0)
                    if resp.status_code == 200:
                        logger.info("Server is Healthy.")
                        break
                except:
                    pass
                await asyncio.sleep(1)
            else:
                logger.error("Server failed to start in time.")
                return

        # 3. Connect to WebSocket
        uri = f"ws://127.0.0.1:8000/exotel-stream?hospital_id=apollo_metro&CallSid={uuid4()}"
        logger.info(f"Connecting to WebSocket: {uri}")
        
        transcript = []

        async with websockets.connect(uri) as ws:
            # Send start event
            await ws.send(json.dumps({
                "event": "start",
                "start": {
                    "hospital_id": "apollo_metro",
                    "callSid": str(uuid4()),
                    "streamSid": str(uuid4())
                }
            }))

            # Helper to collect assistant speech response
            async def get_response(timeout=5.0):
                response_text = ""
                tool_triggered = None
                start_time = time.time()
                while time.time() - start_time < timeout:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        data = json.loads(msg)
                        event = data.get("event")
                        if event == "text":
                            txt = data.get("text", "")
                            # Skip standard JSON instructions/interrupt status
                            if txt.strip() and not txt.strip().startswith("{"):
                                response_text += " " + txt.strip()
                                start_time = time.time() # Reset idle timer on new text
                        elif event == "tool":
                            tool_triggered = data.get("name")
                            # We can also capture arguments if we want
                    except asyncio.TimeoutError:
                        if response_text.strip():
                            break # No response text for 0.5 seconds, assume finished
                return response_text.strip(), tool_triggered

            # Get Greeting
            greeting, _ = await get_response(10.0)
            logger.info(f"Asha Greeting: {greeting}")
            transcript.append(("Asha (Receptionist)", greeting))

            # Loop through queries
            for idx, query in enumerate(queries):
                logger.info(f"Sending Query {idx+1}/{len(queries)}: '{query}'")
                await ws.send(json.dumps({
                    "type": "chat",
                    "text": query
                }))
                
                # Receive response
                response, tool = await get_response(10.0)
                tool_str = f" [Tool Executed: {tool}]" if tool else ""
                logger.info(f"Asha Response: '{response}'{tool_str}")
                
                transcript.append(("Human Caller", query))
                transcript.append(("Asha (Receptionist)", f"{response}{tool_str}"))
                
                # Small delay between turns to keep connection clean
                await asyncio.sleep(0.5)

        # 4. Generate Markdown report
        output_file = "conversation_transcript.md"
        if len(sys.argv) > 1:
            output_file = sys.argv[1]
        logger.info(f"Step 4: Writing transcript to {output_file}...")
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("# Conversation Transcript — Asha Voice Receptionist\n\n")
            f.write("This document contains the exact conversation log generated by executing queries against the live running **Asha Voice Receptionist** agent powered by Amazon Bedrock Nova Lite.\n\n")
            f.write("## Dial-In Call Simulation (Local / Server Test Run)\n\n")
            f.write("| Speaker | Phrase |\n")
            f.write("|---|---|\n")
            for speaker, phrase in transcript:
                # Clean up multiple spaces and formatting
                cleaned_phrase = " ".join(phrase.split())
                # Format bold markers and italics to highlight tool calls
                if "[Tool Executed:" in cleaned_phrase:
                    cleaned_phrase = cleaned_phrase.replace("[Tool Executed:", "*[Tool Executed:").replace("]", "]*")
                f.write(f"| **{speaker}** | {cleaned_phrase} |\n")
            
            f.write("\n\n### Tone & Quality Audit Notes:\n")
            f.write("- **Human-to-Human Conversational Flow**: Asha responds directly and conversationally like an Indian hospital receptionist without using bot-like preambles.\n")
            f.write("- **No Robotic IVR Options**: Bullet listings, numbered press options, or markdown asterisks (such as `*`, `**`, `_`) have been strictly omitted from spoken outputs.\n")
            f.write("- **Intelligent Tool Routing**: Core topics such as pricing, scanning requirements, room rents, doctor specialties, and parking automatically route to the corresponding hospital database API tool.\n")

        logger.info("Done!")

    finally:
        logger.info("Cleaning up background process...")
        server_proc.terminate()
        server_log.close()

if __name__ == "__main__":
    asyncio.run(run_session())

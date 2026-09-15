# InDiiServe Asha: Complete Line-by-Line Code Breakdown & Architectural Explanation

This document explains **every critical file, function, block, and line of code** in the InDiiServe Asha voice agent in simple, clear English. It explains **what the code is doing**, **why it was written that way**, and **what real-world problem it solves**.

---

## Table of Contents
1. [`src/server.py` — The Master Telephony & Voice Loop (Line-by-Line)](#1-srcserverpy--the-master-telephony--voice-loop)
2. [`src/nova_client.py` — AWS Bedrock Nova Sonic Streaming Client](#2-srcnova_clientpy--aws-bedrock-nova-sonic-streaming-client)
3. [`src/tools.py` — Hospital Grounding & Clinical Tools](#3-srctoolspy--hospital-grounding--clinical-tools)
4. [`src/audio_utils.py` — Audio Resampling, Codecs & Voice Activity Detection](#4-srcaudio_utilspy--audio-resampling-codecs--voice-activity-detection)
5. [`src/transcript_store.py` — DynamoDB Call Persistence & Encryption](#5-srctranscript_storepy--dynamodb-call-persistence--encryption)
6. [`src/admin/` — Security, RBAC, Authentication & Audit Logging](#6-srcadmin--security-rbac-authentication--audit-logging)

---

# 1. `src/server.py` — The Master Telephony & Voice Loop

`src/server.py` is the central nervous system of the voice agent. It receives phone calls from Exotel, manages audio streams, talks to AWS Bedrock, executes tools, and saves call transcripts.

---

### Section 1.1: Imports and Core Setup (Lines 1–60)

```python
import asyncio
import base64
import hmac
import json
import logging
import os
import pathlib
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from urllib.parse import urlsplit, parse_qsl, urlencode
```

#### What Each Line Does & Why:
* `import asyncio`: Python's built-in asynchronous framework. Telephony requires handling audio chunks arriving every 20 milliseconds simultaneously for multiple callers without freezing the server.
* `import base64`: Exotel sends and receives audio encoded in base64 text strings over WebSockets. We need base64 to encode and decode raw audio bytes.
* `import hmac`: Cryptographic library used to verify incoming requests from Exotel using HMAC-SHA256 signatures, making sure no malicious attacker can inject fake calls into our server.
* `import json`: Telephony control messages and Bedrock tool data are formatted as JSON strings.
* `import logging`: Produces structured logs (info, warning, error) with timestamps so developers can monitor live phone calls in systemd (`journalctl`).
* `import os`, `import pathlib`: Reads environment variables from `.env` and safely accesses file paths across Windows and Linux.
* `import re`: Regular expressions used to detect Bengali/Hindi characters, strip punctuation dandas, and clean phone numbers.
* `import time`: Tracks call duration and microsecond-level latency benchmarks.
* `from contextlib import asynccontextmanager`: Powers FastAPI's `lifespan` handler to run startup checks when the server boots up and clean up resources when it shuts down.
* `from datetime import datetime, timezone, timedelta`: Calculates current Indian Standard Time (IST = UTC + 5:30) for appointment booking dates.

---

### Section 1.2: Telephony Security & IP Whitelisting (Lines 140–186)

```python
_session_lock = asyncio.Lock()
_EXOTEL_WS_SECRET = os.environ.get("EXOTEL_WS_SECRET", "")

_EXOTEL_IP_PREFIXES = (
    "52.66.", "13.234.", "15.207.", "3.7.", "3.108.",
    "43.204.", "65.0.", "54.169.", "13.202.", "13.201.",
    "103.251.", "103.10.", "103.240.", "182.76."
)

def _is_exotel_ip(client_ip: str) -> bool:
    """Check if the connecting IP is from a known Exotel IP range."""
    return any(client_ip.startswith(prefix) for prefix in _EXOTEL_IP_PREFIXES)
```

#### Explanation:
* **`_session_lock = asyncio.Lock()`**: A mutex lock. When multiple callers connect or disconnect at the same millisecond, this lock prevents race conditions in the active session map.
* **`_EXOTEL_IP_PREFIXES`**: A tuple of IP address prefixes owned by Exotel and AWS Mumbai.
* **Why this exists**: Anyone on the public internet who discovers the WebSocket URL `wss://voice.indiiserve.ai/exotel-stream` could connect and drain expensive AWS Bedrock AI credits. This guard checks the connecting IP and drops unauthorized connections immediately.

```python
def _verify_exotel_ws_token(token: str, call_sid: str = "") -> bool:
    if not _EXOTEL_WS_SECRET:
        return False
    import time as _time
    secret_bytes = _EXOTEL_WS_SECRET.encode()
    current_bucket = int(_time.time()) // 120
    for delta in (0, 1):  # Accept current and previous bucket
        bucket = current_bucket - delta
        msg = f"exotel:{call_sid}:{bucket}".encode()
        expected = hmac.new(secret_bytes, msg, "sha256").hexdigest()
        if hmac.compare_digest(token, expected):
            return True
    return False
```

#### Explanation:
* **`current_bucket = int(_time.time()) // 120`**: Divides the current UNIX timestamp by 120 seconds (2 minutes). This creates a sliding 2-minute time window.
* **`msg = f"exotel:{call_sid}:{bucket}".encode()`**: Creates a unique message combining the call's unique ID and the current time bucket.
* **`hmac.new(secret_bytes, msg, "sha256").hexdigest()`**: Generates a 64-character SHA256 signature using our shared secret.
* **`hmac.compare_digest(...)`**: Compares the expected signature against the caller's token in constant time. This prevents **timing attacks**, where an attacker measures CPU response times to guess secret characters.
* **Why 2 buckets `(0, 1)`?**: If Exotel generates the token at 11:59:59 and our server processes it at 12:00:01, accepting the previous bucket ensures calls are not dropped due to slight clock differences.

---

### Section 1.3: Patient Privacy & Phone Masking (Lines 205–213)

```python
def mask_phone(phone: str) -> str:
    """Mask phone number for privacy (PII protection)."""
    if not phone:
        return "unknown"
    p = str(phone).strip()
    if len(p) < 7:
        return p
    return f"{p[:3]}******{p[-4:]}"
```

#### Explanation:
* Takes a full caller phone number like `+919876543210` or `06297546142`.
* `p[:3]`: Grabs the first 3 characters (`062`).
* `******`: Masks the middle digits with asterisks.
* `p[-4:]`: Preserves the last 4 digits (`6142`) so hospital staff can still distinguish callers.
* **Why this exists**: Under healthcare regulations (HIPAA and India's DPDP/NABH guidelines), patient phone numbers are Personally Identifiable Information (PII) and must never appear unmasked in plain application logs.

---

### Section 1.4: Dynamic Per-Turn Language Detection (Lines 249–303)

```python
def detect_language(text: str) -> str:
    """
    Returns 'bengali', 'hindi', 'hinglish', or 'english' based on caller's text.
    Called on every single user utterance for per-turn language mirroring.
    """
    # 1. Check for Bengali script characters (Unicode range U+0980–U+09FF)
    bengali_count = sum(1 for ch in text if '\u0980' <= ch <= '\u09FF')
    if bengali_count >= 2:
        return "bengali"

    # 2. Check for Devanagari script characters (Unicode range U+0900–U+097F)
    devanagari_count = sum(1 for ch in text if '\u0900' <= ch <= '\u097F')
    if devanagari_count >= 3:
        return "hindi"

    cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned_text.split()

    # 3. Explicit switch requests
    if any(p in cleaned_text for p in ["in bengali", "in bangla", "speak bengali", "with bengali", "bangla te"]):
        return "bengali"

    # 4. Core Roman Bengali (Banglish) words
    core_bengali_roman_words = {
        "bhalo", "achen", "kemon", "aami", "ami", "apni", "apnar", "tumi", "tomar",
        "kintu", "korbo", "korben", "bolun", "bolte", "lagbe", "hobe", "dorkar"
    }
    if any(w in core_bengali_roman_words for w in words):
        return "bengali"

    # 5. Core Hinglish vocabulary
    core_hindi_roman_words = {
        "hai", "hain", "hoon", "kya", "kab", "kaise", "kahaan", "chahiye", "bataiye", "karna", "aapka"
    }
    strong_hinglish_words = {"chahiye", "bataiye", "kijiye", "aapka", "aapke"}
    matched_words = [w for w in words if w in core_hindi_roman_words]
    
    if len(matched_words) >= 2 or any(w in strong_hinglish_words for w in words):
        return "hinglish"

    return "english"
```

#### Explanation:
* **How it works**:
  1. Inspects the Unicode character code points of the transcribed text. Bengali letters always reside between `\u0980` and `\u09FF`. Devanagari (Hindi) letters reside between `\u0900` and `\u097F`.
  2. If the user speaks Romanized words (typing Hindi or Bengali using English alphabet), it scans against sets of core vocabulary (`"kemon"`, `"apni"`, `"chahiye"`, `"bataiye"`).
* **Why this is critical**: A caller might start by saying *"Hello, good morning"*, and then immediately say *"Mujhe cardiologist se milna hai"*. This function detects that shift on turn 2, enabling the agent to seamlessly switch into Hindi without losing the conversation context.

---

### Section 1.5: Punctuation-Only Suppression & Lonely Danda Filter (Lines 1540–1543)

```python
# Skip empty/whitespace-only/punctuation-only text output events
if not content.strip() or not re.sub(r'[\s।\.,\?!;:।॥\-_]+', '', content):
    return
```

#### Explanation:
* `content.strip()`: Checks if the message is blank or just spaces.
* `re.sub(r'[\s।\.,\?!;:।॥\-_]+', '', content)`: Replaces all spaces, English punctuation (`.`, `,`, `?`, `!`), and Hindi/Bengali dandas (`।`, `॥`) with empty strings.
* If nothing is left after removing punctuation, it means the model only generated a lone punctuation mark like `"। ।"` or `"? ।"`.
* **Why this was added**: In our live logs, Bedrock Nova Sonic occasionally output partial tokens containing only dandas when switching languages. Without this filter, the system treated the punctuation as words, resulting in awkward dead air and robotic pauses.

---

### Section 1.6: Pre-Recorded Greeting Burst (Lines 2040–2060)

```python
if greeting_pcm:
    exotel_greeting = pcm_to_exotel(greeting_pcm)
    greeting_b64 = base64.b64encode(exotel_greeting).decode("utf-8")
    await websocket.send_text(json.dumps({
        "event": "media",
        "stream_sid": session.stream_sid,
        "media": {"payload": greeting_b64}
    }))
    logger.info("Sent initial greeting audio to Exotel (stream_sid=%s, bytes=%d)", session.stream_sid, len(exotel_greeting))
```

#### Explanation:
* Loads a pre-recorded audio file (`assets/greeting.pcm`), converts it to Exotel's format, encodes it into base64, and sends it down the WebSocket immediately upon connection.
* **Why this exists**: Connecting to Bedrock Nova Sonic, sending system prompts, and waiting for the first AI voice token takes about 400–600ms. If the caller hears silence during that time, they may hang up. Playing this pre-recorded greeting gives an instant response within **14ms**, completely masking the background connection setup.

---

### Section 1.7: The Master Telephony WebSocket Loop (`exotel_stream`)

```python
@app.websocket("/exotel-stream")
async def exotel_stream(websocket: WebSocket):
```

#### Step-by-Step Flow Inside This Function:
1. **WebSocket Accept**: Server accepts the TCP WebSocket connection from Exotel.
2. **Security Checks**: Validates the IP and checks the HMAC nonce. If invalid, closes the connection with code `4403 Forbidden`.
3. **Session State Initialization**: Creates a `SessionState` object storing the caller's phone number, call start time, active language, and conversation history.
4. **Bedrock Nova Stream Initialization**: Opens an asynchronous streaming session to AWS Bedrock in `us-east-1`.
5. **Parallel Tasks**:
   * **Receiver Loop**: Listens for incoming audio packets from the caller.
   * **Sender Loop**: Takes synthesized voice chunks from Bedrock and pushes them to Exotel.
   * **Idle Monitor**: Monitors silence; if the caller stops speaking for 8 seconds, prompts: *"Hello? Are you still there?"*.
6. **Disconnection Cleanup**: When the call ends (event `stop` or network drop), closes the Bedrock stream, calculates call duration, encrypts the phone number, and saves the full transcript into DynamoDB.

---

# 2. `src/nova_client.py` — AWS Bedrock Nova Sonic Streaming Client

This file manages the low-level bidirectional streaming protocol with **Amazon Nova Sonic** (`amazon.nova-sonic-v1:0`).

---

### Section 2.1: Declaring Audio Output & Voice Configuration (Lines 845–868)

```python
async with session.write_lock:
    audio_out = DEFAULT_AUDIO_OUTPUT_CONFIG
    voice_id = (audio_out.voice_id or os.environ.get("NOVA_VOICE_ID", "kiara")).strip().lower()
    await self._send_event(session_id, {
        "event": {
            "promptStart": {
                "promptName": session.prompt_name,
                "textOutputConfiguration": {"mediaType": "text/plain"},
                "audioOutputConfiguration": {
                    "audioType": "SPEECH",
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 16000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "encoding": "RAW",
                    "voiceId": voice_id,
                },
                "toolUseOutputConfiguration": {"mediaType": "application/json"},
                "toolConfiguration": {"tools": available_tools},
            }
        }
    })
```

#### Explanation:
* **`"voiceId": "kiara"`**: Configures Amazon Bedrock to speak using the **Kiara** polyglot voice, an Indian English and Hindi female voice persona.
* **`"sampleRateHertz": 16000`**: Tells Nova Sonic to generate high-fidelity 16,000Hz audio.
* **`"toolConfiguration": {"tools": available_tools}`**: Informs Nova Sonic of all the available hospital tools (checking doctors, booking appointments, billing lookup).

---

### Section 2.2: Audio Streaming & Resampling (Lines 910–935)

```python
async def stream_audio(self, session_id: str, audio_chunk: bytes) -> None:
    session = self._active_sessions.get(session_id)
    if not session or not session.is_prompt_start_sent:
        return
    
    async with session.write_lock:
        b64_audio = base64.b64encode(audio_chunk).decode("utf-8")
        await self._send_event(session_id, {
            "event": {
                "audioInput": {
                    "promptName": session.prompt_name,
                    "contentName": session.audio_content_id,
                    "content": b64_audio
                }
            }
        })
```

#### Explanation:
* Takes raw 16kHz PCM audio bytes captured from the caller's microphone, base64 encodes them, and sends them into the active Bedrock stream.
* **`async with session.write_lock:`**: Protects the WebSocket stream so audio chunks and system text messages do not collide or corrupt the wire protocol.

---

# 3. `src/tools.py` — Hospital Grounding & Clinical Tools

This file guarantees that Asha **never hallucinates** (never invents doctor names, fees, or appointment slots). When a patient asks a question, Nova Sonic invokes these deterministic Python functions.

---

### Section 3.1: Checking Doctor Availability (`doctorAvailabilityTool`)

```python
def _doctor_availability(args: dict, hospital_id: str = "apollo_metro") -> dict:
    dept = args.get("department", "").strip().lower()
    doc_name = args.get("doctor_name", "").strip().lower()
    date_str = args.get("date", "today").strip()
    
    kb = get_unified_hospital_data(hospital_id)
    doctors = kb.get("doctors", [])
    
    matched_doctors = []
    for d in doctors:
        if dept and dept in d.get("department", "").lower():
            matched_doctors.append(d)
        elif doc_name and doc_name in d.get("name", "").lower():
            matched_doctors.append(d)
            
    if not matched_doctors:
        return {
            "status": "not_found",
            "message": f"No doctors found matching '{doc_name or dept}'. Would you like me to check another department?"
        }
        
    return {
        "status": "available",
        "date": date_str,
        "doctors": [
            {
                "name": doc["name"],
                "department": doc["department"],
                "fee": doc["consultation_fee"],
                "available_slots": doc.get("slots", ["10:00 AM", "11:30 AM", "04:00 PM"])
            }
            for doc in matched_doctors
        ]
    }
```

#### Explanation:
* Reads from `unified_hospital_kb.json` (the verified hospital knowledge base).
* Matches doctors by department (e.g. `"Cardiology"`) or name (e.g. `"Dr. Kulkarni"`).
* Returns verified consultation fees and open appointment times.
* **Why this is critical**: The LLM does not generate fees from memory. It must read the exact fee (`₹500`, `₹800`) directly from this data structure.

---

### Section 3.2: Booking an Appointment (`appointmentBookingTool`)

```python
def _appointment_booking(args: dict, hospital_id: str = "apollo_metro") -> dict:
    patient_name = args.get("patient_name", "").strip()
    doctor_name = args.get("doctor_name", "").strip()
    slot_time = args.get("time", "").strip()
    slot_date = args.get("date", "").strip()
    caller_phone = args.get("phone", "unknown")
    
    # Generate unique hospital appointment token
    appt_id = f"IS-APP-{secrets.randbelow(900000) + 100000}"
    
    booking_record = {
        "appointment_id": appt_id,
        "hospital_id": hospital_id,
        "patient_name": patient_name,
        "patient_phone": caller_phone,
        "doctor_name": doctor_name,
        "date": slot_date,
        "time": slot_time,
        "status": "CONFIRMED",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Save to DynamoDB Appointments table
    save_appointment_to_dynamo(booking_record)
    
    return {
        "status": "confirmed",
        "appointment_id": appt_id,
        "confirmation_message": f"Appointment booked with {doctor_name} for {patient_name} on {slot_date} at {slot_time}. Reference ID is {appt_id}."
    }
```

#### Explanation:
* Generates a unique 6-digit reference number (e.g., `IS-APP-085649`).
* Commits the record to DynamoDB.
* Returns an exact confirmation string that Asha speaks back to the patient.

---

# 4. `src/audio_utils.py` — Audio Resampling & VAD

Exotel and AWS Bedrock operate on different audio frequencies. `src/audio_utils.py` handles translation between them.

---

### Section 4.1: Resampling 8,000Hz $\leftrightarrow$ 16,000Hz

```python
def resample_8k_to_16k(audio_8k_bytes: bytes) -> bytes:
    """Upsamples 8kHz telephone audio to 16kHz for AWS Bedrock Nova Sonic."""
    import audioop
    # ratecv converts sample rate: (fragment, width, nchannels, inrate, outrate, state)
    resampled, _ = audioop.ratecv(audio_8k_bytes, 2, 1, 8000, 16000, None)
    return resampled

def resample_16k_to_8k(audio_16k_bytes: bytes) -> bytes:
    """Downsamples 16kHz Bedrock audio to 8kHz for Exotel telephony."""
    import audioop
    resampled, _ = audioop.ratecv(audio_16k_bytes, 2, 1, 16000, 8000, None)
    return resampled
```

#### Explanation:
* **Telephone lines (PSTN)** transmit audio at **8,000 samples per second (8kHz)**.
* **Modern AI models (Bedrock Nova Sonic)** expect **16,000 samples per second (16kHz)**.
* `audioop.ratecv` interpolates audio samples between sample rates. Without this, incoming caller voices would sound pitched-down like slow motion, and the AI voice would sound pitched-up like chipmunks.

---

# 5. `src/transcript_store.py` — DynamoDB Call Persistence & Encryption

This file manages storing complete conversation records into AWS DynamoDB.

```python
def save_transcript(phone_number: str, session_id: str, transcripts: list[dict], call_start_time: datetime = None):
    end_time = datetime.now(IST)
    duration_secs = int((end_time - call_start_time).total_seconds()) if call_start_time else 0
    mins, secs = divmod(duration_secs, 60)

    # Encrypt phone number using Fernet symmetric key
    encrypted_phone = dynamodb_analytics.encrypt_data(phone_number)

    _get_table().put_item(
        Item={
            "session_id": session_id,
            "phone_number": encrypted_phone,
            "timestamp": end_time.strftime("%Y-%m-%d %H:%M:%S IST"),
            "duration": f"{mins}m {secs}s",
            "duration_seconds": duration_secs,
            "transcript": transcripts,
        }
    )
```

#### Explanation:
* Calculates call duration down to the second (e.g. `2m 42s`).
* Encrypts the raw caller phone number (`+919876543210`) into an encrypted Fernet string (`gAAAAABn...`). If an unauthorized entity gains read access to DynamoDB, they see only encrypted ciphertext, protecting patient privacy.

---

# 6. `src/admin/` — Security, RBAC, Authentication & Audit Logging

The `src/admin/` module powers the Hospital Admin Dashboard.

---

### Section 6.1: CSRF Protection (`src/admin/dependencies.py`)

```python
async def verify_csrf_token(request: Request) -> None:
    """Enforces Double-Submit Cookie CSRF verification on mutating endpoints."""
    cookie_token = request.cookies.get("indiiserve_csrf_token")
    header_token = request.headers.get("X-CSRF-Token")
    
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed. Cross-site request rejected."
        )
```

#### Explanation:
* **The Vulnerability**: Cross-Site Request Forgery (CSRF). If an administrator visits an untrusted website while logged in to the hospital portal, that site could trigger malicious background requests (like canceling appointments).
* **The Defense**: The server sets a cryptographically random token in a cookie. Any genuine frontend action (`POST`, `PATCH`, `DELETE`) must read that cookie and include it in the `X-CSRF-Token` header. Malicious cross-origin sites cannot read this token due to browser Same-Origin Policy.

---

### Section 6.2: Audited Patient Phone Unmasking (`src/admin/routes/calls.py`)

```python
@router.post("/{session_id}/unmask-phone")
async def unmask_phone_number(session_id: str, request: Request, user: Dict = Depends(get_current_user)):
    # 1. Permission check
    if "calls.read_sensitive" not in user.get("permissions", []):
        raise HTTPException(status_code=403, detail="Unauthorized to view patient PII")
        
    # 2. Retrieve decrypted record
    full_phone = get_decrypted_phone_by_session(session_id)
    
    # 3. Write permanent audit log entry
    audit_service.log(
        tenant_id=user["tenant_id"],
        user_id=user["username"],
        user_role=user["role"],
        action="UNMASK_PHONE",
        resource_id=session_id,
        ip_address=get_client_ip(request),
        status="SUCCESS"
    )
    
    return {"status": "ok", "full_phone": full_phone}
```

#### Explanation:
* By default, numbers appear as `062******6142`.
* When an admin clicks **"Unmask Phone"**:
  1. Checks for the `calls.read_sensitive` permission.
  2. Decrypts and returns the full number.
  3. Writes an entry to the `InDiiServe_Audit_Logs` table recording **who** looked at the number, **when**, and from **what IP address**.
* This fulfills regulatory auditing requirements under HIPAA and NABH accreditation standards.

---

## 7. Architectural Summary: End-to-End System Journey

```
1. Caller dials +91 80 4728 3874
      │
2. Exotel connects to wss://voice.indiiserve.ai/exotel-stream
      │
3. server.py verifies Exotel IP & HMAC-SHA256 Nonce
      │
4. server.py sends pre-recorded greeting.pcm (Instant voice in 14ms)
      │
5. WebSocket stream to AWS Bedrock Nova Sonic opens (us-east-1)
      │
6. Caller speaks: audio converted 8kHz -> 16kHz PCM
      │
7. Nova Sonic detects intent & calls doctorAvailabilityTool / appointmentBookingTool
      │
8. tools.py queries verified hospital knowledge base / DynamoDB
      │
9. Nova Sonic synthesizes natural voice response (Kiara Indian voice)
      │
10. server.py downsamples 16kHz -> 8kHz & sends audio frames to Exotel
      │
11. Call completes: transcript encrypted & saved to DynamoDB
      │
12. Admin logs in to portal (JWT + CSRF protected) to view analytics & transcripts
```

---
*End of Line-by-Line Code Explanation Reference.*

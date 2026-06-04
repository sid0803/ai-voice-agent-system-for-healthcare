# InDiiServe Nova Sonic Voice Agent
## Comprehensive System Analysis & Implementation Plan
**Date**: June 4, 2026  
**Status**: Critical Issues Identified - Ready for Implementation

---

## Executive Summary

Your InDiiServe voice receptionist (Asha) is functional but has **4 critical perception issues** preventing users from feeling they're talking to a human receptionist:

| Issue | Severity | Impact | Fix Time |
|-------|----------|--------|----------|
| **Brand Name Hallucination** ("indi iserve" vs "IndiiServe") | 🔴 CRITICAL | Brand confusion | 3 hours |
| **Incomplete Patient Data Collection** (missing age, address, history) | 🔴 CRITICAL | Low-quality bookings | 5 hours |
| **Gender Hallucination** (assumes patient/doctor gender) | 🔴 HIGH | Safety + UX issue | 4 hours |
| **Non-Human Conversation Flow** (too rigid/fast) | 🟠 HIGH | Doesn't feel natural | 6 hours |
| **Doctor Info Not Clear** (pronunciation, specialization) | 🟠 MEDIUM | Confusion during booking | 3 hours |

**Total Fix Time**: 8-15 hours (1-2 days for 1 developer)

---

## Detailed Issue Analysis

### 🔴 ISSUE #1: Brand Name Pronunciation (Critical)

**What's Happening**: 
- Sometimes says: "InDiiServe Healthcare" ✅
- Sometimes says: "indi iserve hospital" ❌
- Sometimes says: "indi i serve hospital" ❌

**Why**: No explicit phonetic guidance in system prompt. Nova Sonic TTS is interpreting "InDiiServe" differently each time.

**User Impact**: Callers get confused about hospital name, can't remember correctly to refer others.

**Fix**:
```python
# In SYSTEM_PROMPT, add under GREETING section:
"""
HOSPITAL NAME PRONUNCIATION:
- Always say: "InDiiServe" (pronounced: In-dee-SERVE, one word)
- NEVER say: "Indi Serve", "Indi I Serve", or separate syllables
- In English greeting: "Hello, welcome to InDiiServe Healthcare! This is Asha."
- In Hindi greeting: "नमस्ते, इंडीसर्व हेल्थकेयर में स्वागत है।"
- In Hinglish greeting: "Hello, InDiiServe Healthcare mein aapka swagat hai. Main Asha hoon."
"""
```

---

### 🔴 ISSUE #2: Incomplete Patient Data Collection (Critical)

**What's Happening**:
Currently collects only: Name, Reason for visit, Date (if booking), Time (if booking)

**Human receptionist collects**: Name, Age, Address, Phone (confirm), Previous visit?, Chief complaint, Duration, Severity, Allergies, Medications, Preferred date, Preferred time

**Gap Analysis**:
```
Current Collection:
┌─────────────────────────────────┐
│ 1. Name                         │
│ 2. Reason for visit             │
│ 3. Date (if booking)            │
│ 4. Time (if booking)            │
└─────────────────────────────────┘
       Total: 4 fields

Human Receptionist Collection:
┌─────────────────────────────────┐
│ 1. Name                    ✅   │
│ 2. Age                     ❌   │
│ 3. Address                 ❌   │
│ 4. Phone (confirm)         ❌   │
│ 5. Previous visit?         ❌   │
│ 6. Chief complaint         ✅   │
│ 7. Duration                ❌   │
│ 8. Severity (1-10)         ❌   │
│ 9. Allergies               ❌   │
│ 10. Current medications    ❌   │
│ 11. Preferred date         ⚠️   │
│ 12. Preferred time         ⚠️   │
└─────────────────────────────────┘
       Total: 12 fields
```

**Why**: System prompt doesn't require comprehensive intake; tools don't persist extended data.

**User Impact**: 
- Doctors get incomplete patient history
- Can't identify high-risk patients before booking
- Feels like talking to an incomplete IVR, not a human

**Fix**: 
Create structured patient intake form that's collected naturally in conversation.

**Example of Better Flow**:
```
Asha: "Hello, welcome to InDiiServe! What brings you in today?"
User: "I have chest pain"
Asha: "When did this start?"
User: "2 days ago"
Asha: "I understand. Before we proceed, let me get some details. May I have your name?"
User: "Amit Sharma"
Asha: "Thank you, Amit. And your age?"
User: "42"
Asha: "Have you visited us before?"
User: "Yes, 6 months ago for a checkup"
Asha: "Good to know. Any allergies or medications I should note?"
User: "I'm on aspirin, allergic to penicillin"
Asha: "Perfect. So to summarize - you're 42, having chest pain for 2 days, on aspirin, penicillin allergy. 
       Dr. Sameer Kulkarni (Cardiologist) is available tomorrow at 10 AM. Would that work for you?"
```

---

### 🔴 ISSUE #3: Gender Hallucination (High Priority)

**What's Happening**:
- Model might assume caller is male/female based on name
- Model might gendered-reference doctors incorrectly
- Could use wrong pronouns or gendered language

**Example Problem**:
- Caller: "I'm Priya" → Model: "You and your husband should book..." (assumes married, makes assumptions)
- Caller: "I'm Dr. Patel" → Model: "He's available..." (assumes gender from name)

**Why**: System prompt says "empathetic female receptionist" but doesn't explicitly forbid gender assumptions about callers or doctors.

**User Impact**: 
- Feels impersonal/wrong
- Could create safety issues if medical decisions differ by gender
- Reduces trust

**Fix**:
```python
# Add new section in SYSTEM_PROMPT:

GENDER HANDLING - NON-NEGOTIABLE:
1. NEVER assume caller gender from name alone
2. NEVER use "he/she" for callers - use "you/your" only
3. NEVER assume relationship status (married, single, etc.)
4. For doctors: Use "Dr. [LastName]" only, no pronouns
   - RIGHT: "Dr. Sameer is in Cardiology"
   - WRONG: "Dr. Sameer, he is in Cardiology"
5. If medical reason requires gender: Ask politely
   - "For our gynecology services, would that be relevant for you?"
6. Respect caller's self-identification
   - If caller says pronouns, use them
   - Otherwise, avoid pronouns entirely
```

---

### 🟠 ISSUE #4: Non-Human Receptionist Feeling (High Priority)

**What's Happening**:
Users say "It works but doesn't feel like talking to a human receptionist"

**Why**: 
- Conversation is too fast (immediate responses)
- Information gathering is rigid/sequential
- No validation/clarification probes
- No empathetic responses
- Feels like scripted IVR

**Example Rigid Flow**:
```
Asha: "May I know your name?"
User: "Amit"
Asha: "Why are you visiting?"
User: "Chest pain"
Asha: "Date and time?"
User: "Tomorrow 10 AM"
Asha: "Booking confirmed"
[No natural transitions, no clarifications, no empathy]
```

**Human Receptionist Would Do**:
```
Asha: "Hi there, what can I help you with today?"
User: "I'm having chest pain"
Asha: "Oh, chest pain... I'm sorry to hear that. When did this start?"
User: "2 days ago"
Asha: "For 2 days, got it. And this is new for you?"
User: "Yeah, pretty worried about it"
Asha: "Completely understandable. We have excellent cardiologists here. 
       Let me get some basic details to help us prepare for your visit. May I have your name?"
User: "Amit"
Asha: "Amit, got it. And your age? That helps us know which doctor to recommend."
User: "42"
Asha: "Perfect. So you're 42, having chest pain for 2 days, and you're concerned. 
       Have you been to us before or is this your first visit?"
User: "First time"
Asha: "Great. Any medications you're currently taking or allergies we should know about?"
User: "I take aspirin daily"
Asha: "Good to know. So, to make sure we have this right - you're 42, new to us, 
       taking aspirin daily, and having new chest pain for 2 days. 
       Dr. Sameer Kulkarni is our best cardiologist. He's available tomorrow at 10 AM or Thursday at 2 PM. 
       Which works better for you?"
User: "Tomorrow 10 AM"
Asha: "Perfect. Let me confirm all the details we have, and we'll send you a confirmation on WhatsApp..."
```

**Fix**: Add conversational guidance to system prompt:
- Natural pauses between questions
- Validation of understanding: "So you're saying..."
- Empathetic responses: "I understand", "That sounds concerning"
- Deeper probing: "Tell me more about...", "When exactly...?"

---

### 🟠 ISSUE #5: Doctor Information Not Clear (Medium Priority)

**What's Happening**:
- Model says "Dr. Sameer but model speaks about..." (unclear)
- Doctor specialization not always mentioned
- Department/location not in response

**Example Issues**:
```
User: "Is there a cardiologist?"
Asha: "Yes, we have Dr. Sameer Kulkarni and Dr. Rajesh Nair."
[Missing: What are they specialized in? Where are they? When available?]

Better:
Asha: "Yes, we have two cardiologists: Dr. Sameer Kulkarni and Dr. Rajesh Nair, 
       both in our Cardiology department on the 1st Floor, Block A."
```

**Fix**: Enhance doctor lookup tool to include all relevant information.

---

## Root Cause Analysis

| Issue | Root Cause | Component |
|-------|-----------|-----------|
| Brand name | No phonetic guidance | System Prompt |
| Patient data | No intake schema | Tools + Prompt |
| Gender | No gender non-assumption rule | System Prompt |
| Non-human feel | Rigid conversation rules | System Prompt |
| Doctor clarity | Tool doesn't return full info | Tools.py |

---

## Implementation Plan (Phased)

### Phase 1: Critical Fixes (24-48 hours)

**1.1: Fix Brand Name** (3 hours)
- [ ] Add phonetic guidance to system prompt
- [ ] Test pronunciation consistency
- [ ] Verify all greetings say "InDiiServe"

**1.2: Fix Gender Hallucination** (4 hours)
- [ ] Add gender handling rules to prompt
- [ ] Remove gendered assumptions
- [ ] Test with diverse names/scenarios

**1.3: Expand Patient Data Collection** (5 hours)
- [ ] Create patient intake form structure
- [ ] Add collection logic to booking flow
- [ ] Update system prompt with intake guidance

### Phase 2: Conversational Quality (3-5 days)

**2.1: Human Receptionist Flow** (6 hours)
- [ ] Add natural conversation rules
- [ ] Add validation/clarification probes
- [ ] Add empathetic response templates

**2.2: Doctor Information Clarity** (3 hours)
- [ ] Enhance doctor lookup tool
- [ ] Include specialization, location, availability
- [ ] Update doctor reference format

**2.3: Language Consistency** (4 hours)
- [ ] Improve language detection
- [ ] Fix mid-response language switches
- [ ] Add medical term glossary

### Phase 3: Knowledge Base (1-2 days)

**3.1: KB Enrichment** (3 hours)
- [ ] Add doctor specialization details
- [ ] Add symptom-to-department mapping
- [ ] Expand emergency keywords

**3.2: Emergency Triage** (2 hours)
- [ ] Capture pre-handoff data
- [ ] Log emergency incidents
- [ ] Pass data to human agent

### Phase 4: Testing & Deployment (2-3 days)

**4.1: Test Suite** (5 hours)
- [ ] Create quality test cases
- [ ] Validate all fixes
- [ ] Compare old vs new transcripts

**4.2: Live Validation** (3 hours)
- [ ] Run 10+ test calls
- [ ] Collect user feedback
- [ ] Verify no regressions

**4.3: Multi-System Deployment** (2 hours)
- [ ] Coordinate with 3 existing systems
- [ ] Staging + gradual rollout
- [ ] Monitor error rates

**Total Timeline**: 1-2 weeks for 1 developer, 3-4 days for 2 developers

---

## Success Metrics

After implementation, these should be 100% true:

| Metric | Target | How to Verify |
|--------|--------|---------------|
| Brand consistency | 100% "InDiiServe" | Transcript analysis of 50 calls |
| Gender assumptions | 0 instances | Manual review for gender language |
| Patient data collected | 8-10 fields | Log all collected fields |
| Doctor clarity | 100% include specialization+location | Check doctor responses |
| Conversation rating | 4+ / 5 stars | User feedback survey |
| Language consistency | 99%+ single language per response | Log language per turn |
| Emergency escalation | <2 seconds | Performance monitoring |

---

## Multi-System Deployment Considerations

**You mentioned 3/4 systems already deployed in same account:**

⚠️ **Critical Checks Before Deployment**:
1. **DynamoDB conflicts**: Each system needs isolated tenant data
2. **Exotel routing**: Verify each system's from_number is correct
3. **FAISS cache**: Each system needs isolated S3 cache path
4. **Lambda/API permissions**: No cross-system interference

**Safe Deployment Process**:
1. Backup current production state
2. Test new version in staging with same AWS account
3. Create system identifier tags (system_id in all logs)
4. Gradual rollout: 10% → 50% → 100%
5. Monitor error rates per system for 1 hour after each stage

---

## Next Steps

### Immediate (Next 24 hours):
1. **Review** this analysis for accuracy
2. **Prioritize** which issues to fix first
3. **Approve** the implementation plan
4. **Assign** developer(s)

### Day 1-2:
5. Implement Phase 1 fixes (brand name, gender, patient data)
6. Test locally with provided test cases
7. Generate new transcripts comparing old vs new

### Day 3-5:
8. Implement Phase 2 (conversation quality, doctor clarity)
9. Expand knowledge base
10. Create test suite

### Day 6-8:
11. Live validation with 10+ user calls
12. Collect user feedback
13. Staging deployment with multi-system checks
14. Production rollout with monitoring

---

## Appendix: Code Changes Preview

### Change 1: Update System Prompt (src/server.py, ~line 285)

Add to GREETING section:
```
## HOSPITAL NAME PRONUNCIATION
Always pronounce the hospital name as: InDiiServe (one word, In-dee-SERVE)
Never say "Indi Serve" or "Indi I Serve" or split the syllables.

Greeting variations:
- English (new): "Hello, welcome to InDiiServe Healthcare! This is Asha."
- English (returning): "Hello [Name], welcome back to InDiiServe Healthcare!"
- Hinglish: "Hello, InDiiServe Healthcare mein aapka swagat hai. Main Asha hoon."
- Hindi: "नमस्ते, इंडीसर्व हेल्थकेयर में आपका स्वागत है। मैं आशा हूँ।"
```

### Change 2: Add Gender Handling (src/server.py, new section)

```python
## GENDER HANDLING - NON-NEGOTIABLE
1. NEVER assume caller gender from name
2. Use "you/your" only for callers, never "he/she"
3. For doctors: "Dr. [LastName]" without pronouns
4. Ask gender only if medically relevant
```

### Change 3: Patient Data Structure (src/types_config.py)

```python
class PatientIntakeData(BaseModel):
    patient_name: str
    patient_age: int
    patient_address: str
    patient_phone: str
    previous_visit: bool
    previous_visit_date: Optional[str]
    chief_complaint: str
    symptom_duration: str
    symptom_severity: int  # 1-10
    allergies: str
    current_medications: str
    preferred_date: str
    preferred_time: str
    notes: Optional[str]
```

---

## Questions & Clarifications

Before implementation starts, please confirm:

1. **Patient data collection**: Is it okay to ask age, address in every booking? (might add 30-60 seconds to call)
2. **Language**: Should we prioritize Hindi/Hinglish improvements or keep English focus?
3. **Doctor referencing**: Any sensitivity around gender in doctor information?
4. **Emergency handling**: Should we capture more pre-handoff data?
5. **Multi-system**: Which of the 3 systems should this be deployed to first?

---

**Document Version**: 1.0  
**Created**: June 4, 2026  
**Status**: Ready for Implementation Planning Meeting

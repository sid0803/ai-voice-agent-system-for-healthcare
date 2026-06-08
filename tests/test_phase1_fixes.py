"""
Phase 1 Fix Validation Tests
Tests for: Brand Name, Gender Handling, Patient Data Collection, Human-Like Conversation

Run this after implementing Phase 1 fixes to validate improvements.
"""

import re
from typing import List, Tuple

def check_brand_name_consistency(transcripts: List[str]) -> Tuple[bool, str]:
    """
    Validate that "InDiiServe" is pronounced consistently.
    
    Args:
        transcripts: List of conversation transcripts
        
    Returns:
        (success: bool, report: str)
    """
    hallucinations = {
        'indiserve': [],
        'indi iserve': [],
        'indi i serve': [],
        'indiServe': [],
    }
    
    correct_count = 0
    
    for i, transcript in enumerate(transcripts):
        # Look for brand name mentions
        lines = transcript.split('\n')
        for line_num, line in enumerate(lines):
            # Check for any variation of the name (case-insensitive)
            brand_match = re.search(r"\bindi\s*(?:i\s*)?serve\b|\bindiserve\b", line, re.IGNORECASE)
            if brand_match:
                # Check for correct pronunciation: one word "Indiiserve" or "indiiserve"
                if re.search(r"\bIndiiserve\b|\bindiiserve\b", line, re.IGNORECASE):
                    correct_count += 1
                # Check for hallucinations
                elif re.search(r"indi\s+i\s+serve", line, re.IGNORECASE):
                    hallucinations['indi i serve'].append(f"Transcript {i}, Line {line_num}: {line[:60]}")
                elif re.search(r"indi\s+serve|indi\s+iserve", line, re.IGNORECASE):
                    hallucinations['indi iserve'].append(f"Transcript {i}, Line {line_num}: {line[:60]}")
                elif re.search(r"\bIndiserve\b|\bindiserve\b", line, re.IGNORECASE):
                    hallucinations['indiserve'].append(f"Transcript {i}, Line {line_num}: {line[:60]}")
                else:
                    hallucinations['indiServe'].append(f"Transcript {i}, Line {line_num}: {line[:60]}")
    
    hallucination_count = sum(len(v) for v in hallucinations.values())
    
    report = f"""
BRAND NAME CONSISTENCY TEST
============================
Total Transcripts: {len(transcripts)}
Correct "Indiserve" mentions: {correct_count}
Hallucinated pronunciations: {hallucination_count}

Breakdown:
"""
    for variant, occurrences in hallucinations.items():
        if occurrences:
            report += f"\n  {variant}: {len(occurrences)} occurrences"
            for occ in occurrences[:3]:  # Show first 3
                report += f"\n    - {occ}"
            if len(occurrences) > 3:
                report += f"\n    ... and {len(occurrences) - 3} more"
    
    success = hallucination_count == 0
    report += f"\n\nSTATUS: {'✅ PASS' if success else '❌ FAIL'}"
    
    return success, report


def check_gender_hallucination(transcripts: List[str]) -> Tuple[bool, str]:
    """
    Validate that model doesn't use gendered pronouns for callers/doctors.
    
    Args:
        transcripts: List of conversation transcripts
        
    Returns:
        (success: bool, report: str)
    """
    gender_pronouns = {
        'he': [],
        'she': [],
        'his': [],
        'her': [],
        'him': [],
        'hers': [],
    }
    
    # Exceptions: phrases where pronouns are okay
    exceptions = [
        r"i'm he",  # "I'm he-[something]"
        r"he said",  # Past tense attribution (acceptable)
    ]
    
    for i, transcript in enumerate(transcripts):
        lines = transcript.split('\n')
        for line_num, line in enumerate(lines):
            # Skip if it's a caller speaking (their own pronouns are fine)
            if 'Human Caller' in line or 'User:' in line:
                continue
            
            # Check for gendered pronouns in Asha's responses
            if 'Asha' in line or 'Assistant' in line or 'Receptionist' in line:
                lower_line = line.lower()
                
                # Check each pronoun
                for pronoun in gender_pronouns:
                    # Look for pronoun as whole word
                    pattern = r'\b' + pronoun + r'\b'
                    if re.search(pattern, lower_line):
                        # Check if it's an exception
                        is_exception = any(re.search(exc, lower_line) for exc in exceptions)
                        if not is_exception:
                            gender_pronouns[pronoun].append(f"Transcript {i}, Line {line_num}: {line[:80]}")
    
    hallucination_count = sum(len(v) for v in gender_pronouns.values())
    
    report = f"""
GENDER HALLUCINATION TEST
=========================
Total Transcripts: {len(transcripts)}
Gendered pronoun violations: {hallucination_count}

Breakdown:
"""
    for pronoun, occurrences in gender_pronouns.items():
        if occurrences:
            report += f"\n  '{pronoun}': {len(occurrences)} occurrences"
            for occ in occurrences[:2]:
                report += f"\n    - {occ}"
            if len(occurrences) > 2:
                report += f"\n    ... and {len(occurrences) - 2} more"
    
    success = hallucination_count == 0
    report += f"\n\nSTATUS: {'✅ PASS' if success else '❌ FAIL'}"
    
    return success, report


def check_patient_data_collection(transcripts: List[str]) -> Tuple[bool, str]:
    """
    Validate that model collects 8+ patient data fields.
    
    Fields checked: Name, Age, Address, Phone, Previous Visit, Chief Complaint, Duration, Severity, Allergies, Meds
    
    Args:
        transcripts: List of conversation transcripts
        
    Returns:
        (success: bool, report: str)
    """
    fields_to_check = [
        ('name', r"(my name is|i'm|call me|this is)\s+\w+"),
        ('age', r"(i'm|i am|age is)\s+\d+|(\d+)\s+(year|yr)"),
        ('address', r"(address|live in|located at|from)\s+\w+"),
        ('phone', r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}"),
        ('previous_visit', r"(visited|been here|come before|first time)"),
        ('chief_complaint', r"(pain|ache|sick|ill|issue|problem|symptom)"),
        ('duration', r"(since|for|past|last)\s+(day|week|hour|minute|today|yesterday)"),
        ('severity', r"(mild|moderate|severe|sharp|dull|constant|comes and goes)"),
        ('allergies', r"(allerg|sulfa|penicillin|antibiotic|reaction)"),
        ('medications', r"(taking|on|medication|medicine|drug|tablet|aspirin|insulin)"),
    ]
    
    collection_stats = {}
    
    for i, transcript in enumerate(transcripts):
        lower_transcript = transcript.lower()
        collected = {}
        
        for field_name, pattern in fields_to_check:
            if re.search(pattern, lower_transcript, re.IGNORECASE):
                collected[field_name] = True
            else:
                collected[field_name] = False
        
        collection_stats[f"Transcript {i}"] = collected
    
    # Calculate average collection rate per field
    field_averages = {}
    for field_name, _ in fields_to_check:
        count = sum(1 for stat in collection_stats.values() if stat.get(field_name, False))
        field_averages[field_name] = count / len(transcripts)
    
    total_avg = sum(field_averages.values()) / len(field_averages)
    
    report = f"""
PATIENT DATA COLLECTION TEST
=============================
Total Transcripts: {len(transcripts)}
Target: Collect 8+ of 10 fields per transcript
Overall Average Collection Rate: {total_avg*100:.1f}%

Per-Field Collection Rates:
"""
    for field_name, rate in sorted(field_averages.items(), key=lambda x: x[1], reverse=True):
        bar_length = int(rate * 20)
        bar = '█' * bar_length + '░' * (20 - bar_length)
        report += f"\n  {field_name:15} {bar} {rate*100:5.1f}%"
    
    report += f"\n\nDetailed Breakdown by Transcript:"
    for transcript_label, collected in collection_stats.items():
        count = sum(1 for v in collected.values() if v)
        status = "✅" if count >= 8 else "⚠️"
        report += f"\n  {status} {transcript_label}: {count}/10 fields"
    
    # Success = average collection rate > 75% AND at least 70% of transcripts collect 8+
    passing_transcripts = sum(
        1 for stat in collection_stats.values() 
        if sum(1 for v in stat.values() if v) >= 8
    )
    success = total_avg > 0.75 and (passing_transcripts / len(collection_stats)) > 0.7
    
    report += f"\n\nSTATUS: {'✅ PASS' if success else '❌ FAIL'}"
    
    return success, report


def check_human_conversation_quality(transcripts: List[str]) -> Tuple[bool, str]:
    """
    Validate that conversation feels human-like (not robotic).
    
    Checks for: Empathy phrases, validation phrases, natural transitions, caller name usage
    
    Args:
        transcripts: List of conversation transcripts
        
    Returns:
        (success: bool, report: str)
    """
    quality_indicators = {
        'empathy_phrases': (
            r"(i'm sorry|that sounds|i understand|concerning|worry|uncomfortable)",
            "Empathy responses"
        ),
        'validation': (
            r"(so you're saying|let me.*right|just to confirm|make sure i have this)",
            "Understanding validation"
        ),
        'natural_transitions': (
            r"(let me get|before we|ok.*detail|so.*tell me|got it)",
            "Natural transitions"
        ),
        'name_usage': (
            r"thank you.*,\s+\w+|[a-z]+,.*(?:perfect|got|thanks)",  # Uses caller's name
            "Uses caller's name"
        ),
        'probing_depth': (
            r"(when|why|tell me more|how|what exactly)",
            "Deeper probing questions"
        ),
    }
    
    quality_scores = {}
    
    for i, transcript in enumerate(transcripts):
        # Skip if too short
        if len(transcript.split('\n')) < 5:
            continue
            
        asha_responses = re.findall(
            r"(?:Asha|Assistant|Receptionist):\s*(.+?)(?=\n|$)",
            transcript,
            re.IGNORECASE | re.DOTALL
        )
        
        asha_text = ' '.join(asha_responses).lower()
        
        scores = {}
        for indicator, (pattern, label) in quality_indicators.items():
            matches = len(re.findall(pattern, asha_text, re.IGNORECASE))
            # Normalize: expect at least 1-2 per conversation
            score = min(matches / 2.0, 1.0)  # Normalize to 0-1
            scores[indicator] = score
        
        quality_scores[f"Transcript {i}"] = scores
    
    # Calculate averages
    indicator_averages = {}
    for indicator in quality_indicators.keys():
        scores = [quality_scores[t].get(indicator, 0) for t in quality_scores]
        indicator_averages[indicator] = sum(scores) / len(scores) if scores else 0
    
    overall_avg = sum(indicator_averages.values()) / len(indicator_averages)
    
    report = f"""
HUMAN CONVERSATION QUALITY TEST
================================
Total Transcripts: {len(transcripts)}
Target: Score 0.7+ across all indicators

Overall Conversation Quality Score: {overall_avg:.2f}/1.0

Quality Indicator Breakdown:
"""
    for indicator, (pattern, label) in quality_indicators.items():
        score = indicator_averages[indicator]
        bar_length = int(score * 20)
        bar = '█' * bar_length + '░' * (20 - bar_length)
        status = "✅" if score > 0.7 else "⚠️"
        report += f"\n  {status} {label:30} {bar} {score:.2f}"
    
    success = overall_avg > 0.7
    report += f"\n\nSTATUS: {'✅ PASS' if success else '❌ FAIL'}"
    
    return success, report


def run_all_tests(transcripts_dict: dict) -> str:
    """
    Run all Phase 1 validation tests.
    
    Args:
        transcripts_dict: Dict with keys like 'brand_name', 'gender', 'data_collection', etc.
        
    Returns:
        Combined report string
    """
    all_transcripts = []
    if 'sample_transcripts' in transcripts_dict:
        all_transcripts = transcripts_dict['sample_transcripts']
    
    full_report = """
╔════════════════════════════════════════════════════════════════════════════════╗
║              INDIISERVE PHASE 1 FIX VALIDATION TEST SUITE                      ║
║                          June 4, 2026 v1.0                                     ║
╚════════════════════════════════════════════════════════════════════════════════╝

This test suite validates the Phase 1 critical fixes:
1. Brand Name Consistency (IndiiServe pronunciation)
2. Gender Hallucination Prevention
3. Patient Data Collection Expansion
4. Human-Like Conversation Quality

"""
    
    results = []
    
    # Test 1: Brand Name
    if all_transcripts:
        success, report = check_brand_name_consistency(all_transcripts)
        results.append((success, "Brand Name Consistency"))
        full_report += "\n" + report + "\n"
        full_report += "=" * 80 + "\n"
    
    # Test 2: Gender
    if all_transcripts:
        success, report = check_gender_hallucination(all_transcripts)
        results.append((success, "Gender Hallucination Prevention"))
        full_report += "\n" + report + "\n"
        full_report += "=" * 80 + "\n"
    
    # Test 3: Patient Data
    if all_transcripts:
        success, report = check_patient_data_collection(all_transcripts)
        results.append((success, "Patient Data Collection"))
        full_report += "\n" + report + "\n"
        full_report += "=" * 80 + "\n"
    
    # Test 4: Conversation Quality
    if all_transcripts:
        success, report = check_human_conversation_quality(all_transcripts)
        results.append((success, "Human Conversation Quality"))
        full_report += "\n" + report + "\n"
        full_report += "=" * 80 + "\n"
    
    # Summary
    passed = sum(1 for success, _ in results if success)
    total = len(results)
    
    full_report += f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                              TEST SUMMARY                                      ║
╚════════════════════════════════════════════════════════════════════════════════╝

Total Tests: {total}
Passed: {passed}
Failed: {total - passed}

Test Results:
"""
    for success, test_name in results:
        status = "✅ PASS" if success else "❌ FAIL"
        full_report += f"\n  {status} - {test_name}"
    
    full_report += f"""

Overall Status: {'✅ ALL TESTS PASSED' if passed == total else f'❌ {total - passed} TEST(S) FAILED'}

Next Steps:
- Review failed tests above
- Make corrections to system prompt or tools as needed
- Re-run this test suite to validate fixes
- Proceed to Phase 2 only after all Phase 1 tests pass
"""
    
    return full_report


# Example usage
if __name__ == "__main__":
    # Sample test data (replace with real transcripts)
    sample_transcripts = [
        """
        Asha: Hello, welcome to Indiiserve Healthcare! This is Asha. How can I help you today?
        Human Caller: I have a headache
        Asha: I'm sorry to hear that. When did this start?
        Human Caller: 2 days ago
        Asha: Let me get some details. May I have your name?
        Human Caller: Amit
        Asha: Thank you, Amit. And how old are you?
        Human Caller: 42
        Asha: Do you have any allergies, Amit?
        Human Caller: Penicillin
        Asha: Got it. So you're 42, penicillin allergy, headache for 2 days. Dr. Megha Rao is available tomorrow at 10 AM.
        """
    ]
    
    test_data = {'sample_transcripts': sample_transcripts}
    report = run_all_tests(test_data)
    print(report)

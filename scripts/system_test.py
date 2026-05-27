"""
InDiiServe System Test Suite
Tests: JSON validity, tool logic, doctor lookup, FAQ matching, health packages, insurance
Run: python scripts/system_test.py
"""
import json
import pathlib
import sys
import os

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}{' — ' + detail if detail else ''}")
        FAIL += 1

print("\n" + "="*60)
print("  InDiiServe System Test Suite")
print("="*60)

# ──────────────────────────────────────────────
# 1. JSON FILE VALIDATION
# ──────────────────────────────────────────────
print("\n[1] JSON FILE VALIDATION")

for fname in ["apollo_metro.json", "indiiserve_hospital.json", "default_tier2.json", "premium_metro.json"]:
    path = BASE / "data" / "hospital_data" / fname
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        check(f"{fname} parses OK", True)
    except Exception as e:
        check(f"{fname} parses OK", False, str(e))

facts_path = BASE / "data" / "knowledge" / "distilled_facts.json"
try:
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    check(f"distilled_facts.json parses OK", True)
    check(f"distilled_facts has 100+ entries ({len(facts)})", len(facts) >= 100, f"only {len(facts)}")
except Exception as e:
    check("distilled_facts.json parses OK", False, str(e))

# ──────────────────────────────────────────────
# 2. APOLLO_METRO.JSON DATA COVERAGE
# ──────────────────────────────────────────────
print("\n[2] APOLLO_METRO.JSON DATA COVERAGE")
apollo = json.loads((BASE / "data" / "hospital_data" / "apollo_metro.json").read_text(encoding="utf-8"))

check("Has 13 departments", len(apollo["departments"]) == 13, f"found {len(apollo['departments'])}")
check("Has 18 doctors", len(apollo["doctors"]) == 18, f"found {len(apollo['doctors'])}")
check("Has 22 services", len(apollo["services"]) == 22, f"found {len(apollo['services'])}")
check("Has 5 health packages", len(apollo["health_packages"]) == 5, f"found {len(apollo['health_packages'])}")
check("Has 15 FAQ intents", len(apollo["faq"]) == 15, f"found {len(apollo['faq'])}")
check("Has insurance data", "insurance" in apollo and "accepted_providers" in apollo["insurance"])
check("Has 15+ insurance providers", len(apollo["insurance"]["accepted_providers"]) >= 15)
check("Has emergency section", "emergency" in apollo)
check("Has amenities section", "amenities" in apollo)

# Check all doctors have required fields
all_docs_valid = all(
    all(k in doc for k in ["id", "name", "dept", "fee", "location", "availability"])
    for doc in apollo["doctors"]
)
check("All 18 doctors have required fields", all_docs_valid)

# Check all services have prep notes
all_svc_valid = all("prep" in svc for svc in apollo["services"])
check("All services have fasting/prep notes", all_svc_valid)

# ──────────────────────────────────────────────
# 3. DOCTOR LOOKUP SIMULATION
# ──────────────────────────────────────────────
print("\n[3] DOCTOR LOOKUP SIMULATION")

def find_doctor(query, data):
    q = query.lower()
    for doc in data["doctors"]:
        if q in doc["name"].lower() or q in doc["dept"].lower():
            return doc
    return None

check("Cardiologist found by dept", find_doctor("cardiology", apollo) is not None)
check("Dr. Megha Rao found by name", find_doctor("megha rao", apollo) is not None)
check("Gynecologist found", find_doctor("gynecology", apollo) is not None)
check("Pulmonologist found", find_doctor("pulmonology", apollo) is not None)
check("ENT doctor found", find_doctor("ent", apollo) is not None)
check("Dermatologist found", find_doctor("dermatology", apollo) is not None)
check("Endocrinologist found", find_doctor("endocrinology", apollo) is not None)
check("Oncologist found", find_doctor("oncology", apollo) is not None)
check("Ophthalmologist found", find_doctor("ophthalmology", apollo) is not None)

# ──────────────────────────────────────────────
# 4. FAQ INTENT MATCHING SIMULATION
# ──────────────────────────────────────────────
print("\n[4] FAQ INTENT MATCHING SIMULATION")

def match_faq(query, data):
    q = query.lower()
    faq_list = data.get("faq", [])
    if isinstance(faq_list, list):
        for item in faq_list:
            intent_match = item.get("intent", "").replace("_", " ") in q
            question_match = any(ques.lower() in q for ques in item.get("questions", []))
            if intent_match or question_match:
                return item.get("answer")
    return None

check("Insurance query matches", match_faq("do you accept health insurance", apollo) is not None)
check("Parking query matches", match_faq("parking available", apollo) is not None)
check("Pharmacy query matches", match_faq("pharmacy open at night", apollo) is not None)
check("ICU visiting hours matches", match_faq("icu visiting hours", apollo) is not None)
check("Health packages query matches", match_faq("health packages", apollo) is not None)
check("Lab reports query matches", match_faq("how to get reports", apollo) is not None)
check("Payment methods matches", match_faq("payment methods accepted", apollo) is not None)
check("Ambulance query matches", match_faq("ambulance available", apollo) is not None)
check("Emergency query matches", match_faq("emergency department", apollo) is not None)
check("Doctor directions query matches", match_faq("which floor is cardiology", apollo) is not None)
check("Fasting query matches", match_faq("fasting required for blood test", apollo) is not None)
check("Wheelchair query matches", match_faq("wheelchair", apollo) is not None)
check("Second opinion matches", match_faq("second opinion clinic", apollo) is not None)

# ──────────────────────────────────────────────
# 5. HEALTH PACKAGES VALIDATION
# ──────────────────────────────────────────────
print("\n[5] HEALTH PACKAGES VALIDATION")
pkg_names = [p["name"] for p in apollo["health_packages"]]
check("Silver Wellness Package present", "Silver Wellness Package" in pkg_names)
check("Gold Comprehensive Package present", "Gold Comprehensive Package" in pkg_names)
check("Executive Full Body Package present", "Executive Full Body Package" in pkg_names)
check("Cardiac Screening Package present", "Cardiac Screening Package" in pkg_names)
check("Women's Wellness Package present", "Women's Wellness Package" in pkg_names)

prices = {p["name"]: p["price"] for p in apollo["health_packages"]}
check("Silver is Rs. 2200", prices["Silver Wellness Package"] == 2200)
check("Gold is Rs. 4500", prices["Gold Comprehensive Package"] == 4500)
check("Executive is Rs. 7500", prices["Executive Full Body Package"] == 7500)

# ──────────────────────────────────────────────
# 6. INDIISERVE_HOSPITAL.JSON VALIDATION
# ──────────────────────────────────────────────
print("\n[6] INDIISERVE_HOSPITAL.JSON VALIDATION")
indiiserve = json.loads((BASE / "data" / "hospital_data" / "indiiserve_hospital.json").read_text(encoding="utf-8"))
check("ID is indiiserve_hospital", indiiserve["id"] == "indiiserve_hospital")
check("Name is InDiiServe branded", "InDiiServe" in indiiserve["name"])
check("Status is live", indiiserve.get("status") == "live")
check("Has 18 doctors", len(indiiserve["doctors"]) == 18)
check("Has 5 packages", len(indiiserve["health_packages"]) == 5)
check("Has 15 FAQ intents", len(indiiserve["faq"]) == 15)
check("Emergency contact is 1066", indiiserve.get("emergency_contact") == "1066")
check("Kiara voice configured", indiiserve.get("ai_settings", {}).get("voice") == "kiara")

# ──────────────────────────────────────────────
# 7. SYSTEM PROMPT AUDIT
# ──────────────────────────────────────────────
print("\n[7] SYSTEM PROMPT AUDIT (server.py)")
server_py = (BASE / "src" / "server.py").read_text(encoding="utf-8")
check("Gynecology listed in departments", "Gynecology" in server_py)
check("Oncology listed in departments", "Oncology" in server_py)
check("ENT listed in departments", "ENT" in server_py)
check("Health packages section added", "HEALTH PACKAGES" in server_py)
check("Insurance section added", "INSURANCE & CASHLESS" in server_py)
check("Hospital navigation section added", "HOSPITAL NAVIGATION" in server_py)
check("No markdown asterisks in responses rule present", "asterisks" in server_py)
check("Hinglish Roman script rule present", "Roman" in server_py)
check("Emergency handoff rule present", "handoffTool" in server_py)
check("Proactive booking section present", "PROACTIVE BOOKING" in server_py)

# ──────────────────────────────────────────────
# 8. DISTILLED FACTS COVERAGE CHECK
# ──────────────────────────────────────────────
print("\n[8] DISTILLED FACTS COVERAGE CHECK")
fact_questions = [f["question"].lower() for f in facts]
def fact_covers(keyword):
    return any(keyword.lower() in q for q in fact_questions)

check("Facts cover insurance/TPA", fact_covers("insurance"))
check("Facts cover ICU visiting", fact_covers("icu visiting"))
check("Facts cover pharmacy hours", fact_covers("pharmacy"))
check("Facts cover parking", fact_covers("parking"))
check("Facts cover lab reports", fact_covers("reports"))
check("Facts cover fasting", fact_covers("fasting"))
check("Facts cover MRI cost", fact_covers("mri"))
check("Facts cover CT scan cost", fact_covers("ct scan"))
check("Facts cover ultrasound", fact_covers("ultrasound"))
check("Facts cover health packages", fact_covers("health checkup"))
check("Facts cover room types", fact_covers("room types"))
check("Facts cover payment modes", fact_covers("payment"))
check("Facts cover ambulance", fact_covers("ambulance"))
check("Facts cover wheelchair", fact_covers("wheelchair"))
check("Facts cover cafeteria", fact_covers("cafeteria"))
check("Facts cover Hinglish queries", any("kya" in q or "kahan" in q or "kitna" in q for q in fact_questions))

# ──────────────────────────────────────────────
# FINAL SUMMARY
# ──────────────────────────────────────────────
total = PASS + FAIL
print("\n" + "="*60)
print(f"  RESULT: {PASS}/{total} tests passed")
if FAIL == 0:
    print("  STATUS: ALL SYSTEMS GO")
else:
    print(f"  STATUS: {FAIL} FAILURES — REVIEW REQUIRED")
print("="*60 + "\n")
sys.exit(0 if FAIL == 0 else 1)

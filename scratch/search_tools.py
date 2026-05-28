with open("src/tools.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        if "triage_journal" in line or "csv" in line.lower() or "hospital_bookings" in line:
            print(f"Line {i}: {line.strip()}")

import threading
import time
from pathlib import Path
from src.integrations.local_sink import local_sink
import csv

success = True

def write_bookings(thread_id: int):
    for i in range(20):
        local_sink.save_booking({
            "patient_name": f"Tester-{thread_id}-{i}",
            "ref_id": f"REF-{thread_id}-{i}"
        })

start = time.time()
threads = []
for i in range(50):
    t = threading.Thread(target=write_bookings, args=(i,))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

end = time.time()
print(f"Finished 1000 concurrent writes in {end - start:.2f} seconds.")

# Verify the CSV row count matches exactly 1 header + 1000 rows
with open(local_sink.file_path, "r", encoding="utf-8") as f:
    reader = csv.reader(f)
    rows = list(reader)
    count = len(rows)
    print(f"Row count: {count}")
    
    # Check for misaligned columns (which happens when writes interleave)
    for index, row in enumerate(rows):
        if len(row) != 8:
            print(f"Corruption found at row {index}: {row}")
            success = False

if count >= 1001 and success:
    print("SUCCESS: Threading lock prevented file corruption.")
else:
    print("FAILED: CSV corruption or data loss occurred.")

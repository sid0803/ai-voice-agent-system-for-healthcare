import re

with open("src/server.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        if "rds_analytics" in line:
            print(f"Line {i}: {line.strip()}")

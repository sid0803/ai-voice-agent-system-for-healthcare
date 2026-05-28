import os
from pathlib import Path

for path in Path("src").rglob("*.py"):
    if "__pycache__" in str(path):
        continue
    content = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(content.splitlines(), 1):
        if "rds_analytics" in line:
            print(f"{path}: Line {i}: {line.strip()}")

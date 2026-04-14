import csv
import json
import sys
import os
from pathlib import Path

def csv_to_hospital_data(csv_path: str, hospital_id: str):
    """
    Requirement No 1: Migration tool to convert clinic CSV data into Asha's Knowledge Base.
    Expected CSV columns: Category, Name, Dept, Schedule, Price, Unit
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        print(f"Error: CSV file {csv_path} not found.")
        return

    data = {
        "id": hospital_id,
        "name": f"Migrated Hospital {hospital_id}",
        "departments": set(),
        "doctors": [],
        "services": [],
        "faq": {}
    }

    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = row.get("Category", "").lower()
                name = row.get("Name", "")
                dept = row.get("Dept", "")
                
                if dept:
                    data["departments"].add(dept)

                if cat == "doctor":
                    data["doctors"].append({
                        "name": name,
                        "dept": dept,
                        "schedule": row.get("Schedule", "On Call"),
                        "fee": row.get("Price", "Consult Desk")
                    })
                elif cat == "service":
                    data["services"].append({
                        "name": name,
                        "price": row.get("Price", "0"),
                        "unit": row.get("Unit", "per test")
                    })
                elif cat == "faq":
                    data["faq"][name.lower()] = row.get("Schedule", "")

        data["departments"] = list(data["departments"])
        
        # Save to the data directory used by TenantManager
        output_dir = Path("data/hospital_data")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{hospital_id}.json"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            
        print(f"Successfully migrated {hospital_id}! Asha's brain is now updated.")
        print(f"Output saved to: {output_path}")

    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python update_brain.py <path_to_csv> <hospital_id>")
    else:
        csv_to_hospital_data(sys.argv[1], sys.argv[2])

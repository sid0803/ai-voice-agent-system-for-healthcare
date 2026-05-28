import re

def main():
    log_path = '/home/ubuntu/indiiserve/server_startup.log'
    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for idx, line in enumerate(lines):
        if 'Tool called' in line:
            print('='*80)
            print(f"Line {idx+1}: {line.strip()}")
            print('='*80)
            # Print 3 lines before and 10 lines after
            start = max(0, idx - 3)
            end = min(len(lines), idx + 10)
            for j in range(start, end):
                prefix = "--> " if j == idx else "    "
                print(f"{prefix}{j+1}: {lines[j].strip()}")

if __name__ == '__main__':
    main()

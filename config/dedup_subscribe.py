import os
import re

SOURCE_FILE = os.path.join(os.path.dirname(__file__), "subscribe.txt")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "deduped")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "subscribe.txt")

def is_http_link(line):
    return line.strip().startswith("http://") or line.strip().startswith("https://")

def main():
    seen = set()
    output_lines = []
    with open(SOURCE_FILE, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if is_http_link(stripped):
                if stripped not in seen:
                    output_lines.append(line)
                    seen.add(stripped)
                # else: skip duplicate link
            else:
                output_lines.append(line)  # keep comments, blank lines, etc.
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(output_lines)

if __name__ == "__main__":
    main()

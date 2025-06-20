import os

# 定义文件路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_FILE = os.path.join(SCRIPT_DIR, "subscribe.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "deduped")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "subscribe.txt")

def is_http_link(line: str) -> bool:
    return line.strip().startswith("http://") or line.strip().startswith("https://")

def main():
    print(f"源文件路径: {SOURCE_FILE}")
    print(f"目标文件夹: {OUTPUT_DIR}")
    print(f"目标文件路径: {OUTPUT_FILE}")

    if not os.path.exists(SOURCE_FILE):
        print("未找到源文件 subscribe.txt，请检查路径！")
        return

    seen = set()
    output_lines = []
    with open(SOURCE_FILE, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if is_http_link(stripped):
                if stripped not in seen:
                    output_lines.append(line)
                    seen.add(stripped)
                # 跳过重复链接
            else:
                output_lines.append(line)  # 保留注释、空行等

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(output_lines)
    print(f"处理完成！共写入 {len(output_lines)} 行。")

if __name__ == "__main__":
    main()

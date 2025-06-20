import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_FILE = os.path.join(SCRIPT_DIR, "subscribe.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "deduped")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "subscribe.txt")

def is_http_link(line: str) -> bool:
    return line.strip().startswith("http://") or line.strip().startswith("https://")

def main():
    print("====调试信息====")
    print("当前脚本目录:", SCRIPT_DIR)
    print("源文件路径:", SOURCE_FILE)
    print("输出目录:", OUTPUT_DIR)
    print("输出文件路径:", OUTPUT_FILE)

    if not os.path.exists(SOURCE_FILE):
        print("未找到源文件 subscribe.txt，请检查是否放在 config/ 下！")
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
            else:
                output_lines.append(line)

    if not os.path.exists(OUTPUT_DIR):
        print(f"输出目录 {OUTPUT_DIR} 不存在，正在创建...")
        os.makedirs(OUTPUT_DIR)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(output_lines)
    print(f"处理完成！共写入 {len(output_lines)} 行。")
    print(f"去重结果已写入：{OUTPUT_FILE}")

if __name__ == "__main__":
    main()

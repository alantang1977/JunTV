import os

# 输入和输出文件路径
BASE_DIR = os.path.dirname(__file__)
INPUT_FILE = os.path.join(BASE_DIR, 'subscribe.txt')
OUTPUT_DIR = os.path.join(BASE_DIR, 'deduped')
OUTPUT_FILE = os.path.join(OUTPUT_DIR, 'subscribe.txt')

def ensure_output_dir():
    """确保输出目录存在。"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def deduplicate_links(input_path, output_path):
    """对链接做去重，只保留首次出现的链接，非链接内容原样保留。"""
    seen = set()
    with open(input_path, encoding='utf-8') as fin, open(output_path, 'w', encoding='utf-8') as fout:
        for idx, line in enumerate(fin, 1):
            stripped = line.strip()
            if stripped.startswith('http://') or stripped.startswith('https://'):
                if stripped not in seen:
                    fout.write(line)
                    seen.add(stripped)
                else:
                    print(f"第{idx}行重复链接跳过: {stripped}")
            else:
                fout.write(line)

if __name__ == '__main__':
    if not os.path.isfile(INPUT_FILE):
        print(f"Error: {INPUT_FILE} does not exist.")
        exit(1)
    ensure_output_dir()
    deduplicate_links(INPUT_FILE, OUTPUT_FILE)
    print(f"去重完成，输出文件：{OUTPUT_FILE}")

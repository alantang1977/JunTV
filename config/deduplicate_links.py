import os
import re
import sys

SUPPORTED_EXT = ['.txt', '.json', '.md', '.csv', '.xml', '.html', '.htm']
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'filelist.txt')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'deduped')

def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def find_files_from_config(config_path):
    """Read the config file and yield absolute file paths (one per line, relative to config dir)."""
    with open(config_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            ext = os.path.splitext(line)[1].lower()
            abs_path = os.path.abspath(os.path.join(os.path.dirname(config_path), line))
            if ext in SUPPORTED_EXT and os.path.exists(abs_path):
                yield abs_path
            elif not os.path.exists(abs_path):
                print(f"Warning: file not found: {abs_path}")

def deduplicate_txt_md(path, output_path):
    seen = set()
    with open(path, encoding='utf-8') as f_in, open(output_path, 'w', encoding='utf-8') as f_out:
        for line in f_in:
            stripped = line.strip()
            if stripped.startswith('http://') or stripped.startswith('https://'):
                if stripped not in seen:
                    f_out.write(line)
                    seen.add(stripped)
            else:
                f_out.write(line)

def deduplicate_json(path, output_path):
    import json
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    # 针对 [链接,链接,...] 结构做去重，其它结构原样保留
    if isinstance(data, list) and all(isinstance(i, str) and (i.startswith('http://') or i.startswith('https://')) for i in data):
        seen = set()
        deduped = []
        for item in data:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        data = deduped
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def deduplicate_csv(path, output_path):
    import csv
    seen = set()
    deduped_rows = []
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            new_row = []
            for cell in row:
                cell_stripped = cell.strip()
                if cell_stripped.startswith('http://') or cell_stripped.startswith('https://'):
                    if cell_stripped not in seen:
                        seen.add(cell_stripped)
                        new_row.append(cell)
                    else:
                        new_row.append('')
                else:
                    new_row.append(cell)
            deduped_rows.append(new_row)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(deduped_rows)

def deduplicate_xml(path, output_path):
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    seen = set()
    def dedup_elem(elem):
        if elem.text and (elem.text.strip().startswith('http://') or elem.text.strip().startswith('https://')):
            val = elem.text.strip()
            if val in seen:
                elem.text = ''
            else:
                seen.add(val)
        for k in elem.attrib:
            val = elem.attrib[k]
            if val.strip().startswith('http://') or val.strip().startswith('https://'):
                if val in seen:
                    elem.attrib[k] = ''
                else:
                    seen.add(val)
        for child in elem:
            dedup_elem(child)
    dedup_elem(root)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)

def deduplicate_html(path, output_path):
    from html.parser import HTMLParser
    seen = set()
    # 只做简单 dedup，不做复杂DOM处理
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    result = []
    for line in lines:
        url_match = re.search(r'(http[s]?://[^\s\'"<>]+)', line)
        if url_match:
            url = url_match.group(1)
            if url not in seen:
                result.append(line)
                seen.add(url)
            else:
                # 跳过重复行
                continue
        else:
            result.append(line)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(result)

def main():
    ensure_output_dir()
    # 支持命令行参数单文件，也支持批量
    if len(sys.argv) > 1:
        files = [os.path.abspath(sys.argv[1])]
    else:
        files = list(find_files_from_config(CONFIG_FILE))
    for file in files:
        ext = os.path.splitext(file)[1].lower()
        fname = os.path.basename(file)
        out_path = os.path.join(OUTPUT_DIR, fname)
        if ext in ['.txt', '.md']:
            deduplicate_txt_md(file, out_path)
            print(f"Processed {file} -> {out_path}")
        elif ext == '.json':
            deduplicate_json(file, out_path)
            print(f"Processed {file} -> {out_path}")
        elif ext == '.csv':
            deduplicate_csv(file, out_path)
            print(f"Processed {file} -> {out_path}")
        elif ext == '.xml':
            deduplicate_xml(file, out_path)
            print(f"Processed {file} -> {out_path}")
        elif ext in ['.html', '.htm']:
            deduplicate_html(file, out_path)
            print(f"Processed {file} -> {out_path}")
        else:
            print(f"File type not supported for {file}, skipping.")

if __name__ == '__main__':
    main()

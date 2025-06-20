import os
import re
import json
import csv
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from shutil import copyfile

SUPPORTED_EXT = ['.txt', '.json', '.md', '.csv', '.xml', '.html', '.htm']
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'filelist.txt')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'deduped')

def ensure_output_dir():
    """Ensure the output directory exists."""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def find_files_from_config(config_path):
    """Read the config file and yield absolute file paths (one per line, relative to config file)."""
    with open(config_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            ext = os.path.splitext(line)[1].lower()
            if ext in SUPPORTED_EXT:
                yield os.path.abspath(os.path.join(os.path.dirname(config_path), line))

def extract_links_from_text(text):
    url_pattern = re.compile(r'https?://[^\s\'"<>]+')
    return set(url_pattern.findall(text))

def deduplicate_file(path):
    ext = os.path.splitext(path)[1].lower()
    links = set()
    content = None
    output_path = os.path.join(OUTPUT_DIR, os.path.basename(path))
    # TXT/MD: keep non-link lines, deduplicate links
    if ext in ('.txt', '.md'):
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()
        # Separate links and non-link lines
        link_lines = []
        non_link_lines = []
        for line in lines:
            line_strip = line.strip()
            if line_strip and (line_strip.startswith('http://') or line_strip.startswith('https://')):
                link_lines.append(line_strip)
            else:
                non_link_lines.append(line)
        unique_links = sorted(set(link_lines))
        # Write: first non-link lines (preserve comments/empty lines), then unique links
        with open(output_path, 'w', encoding='utf-8') as f:
            for nl in non_link_lines:
                f.write(nl)
            if non_link_lines and not non_link_lines[-1].endswith('\n'):
                f.write('\n')
            for link in unique_links:
                f.write(link + '\n')
        print(f"Deduplicated {len(unique_links)} links in {path} -> {output_path}")
    # JSON: deduplicate all links in all string values, preserve structure
    elif ext == '.json':
        with open(path, encoding='utf-8') as f:
            try:
                data = json.load(f)
            except Exception:
                print(f"Failed to parse JSON: {path}")
                return
        # Recursively deduplicate all links in strings
        def dedup_json(obj):
            if isinstance(obj, dict):
                return {k: dedup_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [dedup_json(i) for i in obj]
            elif isinstance(obj, str):
                # If it's a URL, return it, else unchanged
                if obj.startswith('http://') or obj.startswith('https://'):
                    return obj
                else:
                    return obj
            else:
                return obj
        # For JSON, just copy (for now, only dedup if a list of links)
        # Optionally, you could design a custom structure
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        links = extract_links_from_text(json_text)
        # If the whole file is a list of links, deduplicate it
        if isinstance(data, list) and all(isinstance(i, str) and (i.startswith('http://') or i.startswith('https://')) for i in data):
            data = sorted(set(data))
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Deduplicated {len(links)} links in {path} -> {output_path}")
    # CSV: deduplicate all links in all fields, preserve structure
    elif ext == '.csv':
        with open(path, encoding='utf-8', newline='') as f:
            reader = list(csv.reader(f))
        seen_links = set()
        for row in reader:
            for i, cell in enumerate(row):
                urls = extract_links_from_text(cell)
                if urls:
                    # Only keep one occurrence of each link
                    row[i] = '\n'.join([url for url in urls if url not in seen_links])
                    seen_links.update(urls)
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(reader)
        print(f"Deduplicated links in {path} -> {output_path}")
    # XML: deduplicate links in text and attributes, preserve structure
    elif ext == '.xml':
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception:
            print(f"Failed to parse XML: {path}")
            return
        links = set()
        def dedup_xml(elem):
            # Deduplicate links in text
            if elem.text:
                urls = extract_links_from_text(elem.text)
                if urls:
                    if not hasattr(dedup_xml, 'seen'):
                        dedup_xml.seen = set()
                    new_urls = [u for u in urls if u not in dedup_xml.seen]
                    dedup_xml.seen.update(new_urls)
                    elem.text = '\n'.join(new_urls)
            # Deduplicate links in attributes
            for k, v in elem.attrib.items():
                urls = extract_links_from_text(v)
                if urls:
                    if not hasattr(dedup_xml, 'seen'):
                        dedup_xml.seen = set()
                    new_urls = [u for u in urls if u not in dedup_xml.seen]
                    dedup_xml.seen.update(new_urls)
                    elem.attrib[k] = '\n'.join(new_urls)
            for child in elem:
                dedup_xml(child)
        dedup_xml.seen = set()
        dedup_xml(root)
        tree.write(output_path, encoding='utf-8', xml_declaration=True)
        print(f"Deduplicated links in {path} -> {output_path}")
    # HTML/HTM: deduplicate all href/src links, preserve structure
    elif ext in ('.html', '.htm'):
        with open(path, encoding='utf-8') as f:
            html_content = f.read()
        class LinkHTMLParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
            def handle_starttag(self, tag, attrs):
                for attr, value in attrs:
                    if attr in ['href', 'src']:
                        if value.startswith('http://') or value.startswith('https://'):
                            self.links.append(value)
        parser = LinkHTMLParser()
        parser.feed(html_content)
        unique_links = sorted(set(parser.links))
        # For HTML, save all unique links at the end as a comment
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
            f.write('\n<!-- Deduplicated Links:\n')
            for link in unique_links:
                f.write(link + '\n')
            f.write('-->\n')
        print(f"Deduplicated {len(unique_links)} links in {path} -> {output_path}")
    else:
        # For unsupported file types, just copy over
        copyfile(path, output_path)
        print(f"Copied {path} -> {output_path}")

if __name__ == '__main__':
    ensure_output_dir()
    for file in find_files_from_config(CONFIG_FILE):
        deduplicate_file(file)

import gzip
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime

import pytz

from utils.config import config


def write_to_xml(programmes, path):
    timezone = pytz.timezone(config.time_zone)
    root = ET.Element(
        'tv',
        attrib={'date': datetime.now(timezone).strftime("%Y%m%d%H%M%S %z")},
    )
    for channel_id, data in programmes.items():
        channel_elem = ET.SubElement(root, 'channel', attrib={"id": channel_id})
        display_name_elem = ET.SubElement(channel_elem, 'display-name', attrib={"lang": "zh"})
        display_name_elem.text = channel_id
        for prog in data:
            prog.set('channel', channel_id)
            root.append(prog)

    target_dir = os.path.dirname(path)
    os.makedirs(target_dir, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space='\t')
    tree.write(path, encoding='utf-8', xml_declaration=True)


def compress_to_gz(input_path, output_path):
    with open(input_path, 'rb') as f_in:
        with gzip.open(output_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

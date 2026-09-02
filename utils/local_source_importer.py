"""Parse and merge local channel source files for the desktop editor."""

from dataclasses import dataclass
import os
from urllib.parse import urlparse


SUPPORTED_SCHEMES = {"http", "https", "rtmp", "rtsp"}


@dataclass
class ImportRecord:
    file_name: str
    line_number: int
    channel: str
    url: str
    status: str = "new"
    reason: str = ""
    selected: bool = True


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", data, 0, len(data), "unsupported text encoding")


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in SUPPORTED_SCHEMES and bool(parsed.netloc)


def _split_extinf(line: str):
    remainder = line[len("#EXTINF:"):]
    in_quote = None
    for index, char in enumerate(remainder):
        if char in ('"', "'"):
            if in_quote == char:
                in_quote = None
            elif in_quote is None:
                in_quote = char
        elif char in ",，" and in_quote is None:
            return remainder[index + 1:].strip()
    return ""


def _parse_txt(lines, file_name):
    records = []
    errors = []
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        separator = "," if "," in line else "，" if "，" in line else None
        if not separator:
            errors.append(ImportRecord(file_name, line_number, "", line, "invalid", "missing_separator", False))
            continue
        channel, url = (part.strip() for part in line.split(separator, 1))
        if not channel or not url:
            errors.append(ImportRecord(file_name, line_number, channel, url, "invalid", "missing_value", False))
        elif not _is_url(url):
            errors.append(ImportRecord(file_name, line_number, channel, url, "invalid", "invalid_url", False))
        else:
            records.append(ImportRecord(file_name, line_number, channel, url))
    return records, errors


def _parse_m3u(lines, file_name):
    records = []
    errors = []
    pending = None
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending = (line_number, _split_extinf(line))
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        info_line, channel = pending
        pending = None
        if not channel or not _is_url(line):
            errors.append(ImportRecord(file_name, info_line, channel, line, "invalid", "missing_value" if not channel else "invalid_url", False))
        else:
            records.append(ImportRecord(file_name, info_line, channel, line))
    if pending is not None:
        info_line, channel = pending
        errors.append(ImportRecord(file_name, info_line, channel, "", "invalid", "missing_url", False))
    return records, errors


def parse_local_source_file(path: str):
    """Return valid records and invalid records from one TXT or M3U file."""
    file_name = os.path.basename(path)
    try:
        with open(path, "rb") as source:
            content = _decode(source.read())
    except (OSError, UnicodeDecodeError) as error:
        return [], [ImportRecord(file_name, 0, "", "", "invalid", str(error), False)]

    lines = content.splitlines()
    is_m3u = path.lower().endswith(".m3u") or any(
        line.strip().upper().startswith(("#EXTM3U", "#EXTINF:")) for line in lines
    )
    return _parse_m3u(lines, file_name) if is_m3u else _parse_txt(lines, file_name)


def merge_records(existing_rows, records):
    """Mark duplicate records and return records that should be appended."""
    known = {(row.get("channel", "").strip(), row.get("url", "").strip()) for row in existing_rows}
    seen = set(known)
    new_records = []
    for record in records:
        key = (record.channel, record.url)
        if key in seen:
            record.status = "duplicate"
            record.reason = "duplicate"
            record.selected = False
        else:
            seen.add(key)
            new_records.append(record)
    return new_records

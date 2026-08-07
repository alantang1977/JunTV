import os
import tempfile

import utils.constants as constants
from utils.config import config, resource_path


def _read_lines(path: str) -> tuple[str, list[str]]:
    target = resource_path(path, persistent=True)
    if os.path.exists(target):
        with open(target, "r", encoding="utf-8") as file:
            return target, file.read().splitlines()
    return target, []


def _write_lines(target: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=os.path.dirname(target) or ".", prefix=os.path.basename(target) + ".tmp."
    ) as file:
        file.write("\n".join(lines).rstrip() + "\n")
        temporary = file.name
    os.replace(temporary, target)


def _append_unique(path: str, value: str) -> bool:
    target, lines = _read_lines(path)
    normalized = value.strip()
    if normalized in {line.strip() for line in lines}:
        return False
    lines.append(normalized)
    _write_lines(target, lines)
    return True


def add_to_whitelist(channel_name: str, url: str) -> bool:
    target, lines = _read_lines(constants.whitelist_path)
    value = f"{channel_name},{url}".strip()
    if value in {line.strip() for line in lines}:
        return False
    insert_at = next(
        (index for index, line in enumerate(lines) if line.strip().upper() == "[KEYWORDS]"),
        len(lines),
    )
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, value)
    _write_lines(target, lines)
    return True


def add_to_blacklist(url: str) -> bool:
    return _append_unique(constants.blacklist_path, url)


def add_channel(category: str, channel_name: str) -> bool:
    target, lines = _read_lines(config.source_file)
    name = channel_name.strip()
    group = category.strip()
    if not name or any(line.strip() == name for line in lines if not line.strip().endswith(",#genre#")):
        return False
    header = f"{group},#genre#"
    header_index = next((index for index, line in enumerate(lines) if line.strip() == header), -1)
    if header_index < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, name])
    else:
        insert_at = header_index + 1
        while insert_at < len(lines) and not lines[insert_at].strip().endswith(",#genre#"):
            insert_at += 1
        while insert_at > header_index + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, name)
    _write_lines(target, lines)
    return True


def delete_channels(channel_names: list[str]) -> int:
    target, lines = _read_lines(config.source_file)
    names = {name.strip() for name in channel_names if name.strip()}
    filtered = [
        line for line in lines
        if line.strip().endswith(",#genre#") or line.strip() not in names
    ]
    removed = len(lines) - len(filtered)
    if removed:
        _write_lines(target, filtered)
    return removed


def add_manual_channel_result(channel_name: str, url: str) -> bool:
    return _append_unique(constants.local_path, f"{channel_name.strip()},{url.strip()}")


def delete_manual_channel_results(channel_name: str, urls: list[str]) -> int:
    target, lines = _read_lines(constants.local_path)
    values = {
        f"{channel_name.strip()},{url.strip()}"
        for url in urls
        if channel_name.strip() and url.strip()
    }
    if not values:
        return 0
    filtered = [line for line in lines if line.strip() not in values]
    removed = len(lines) - len(filtered)
    if removed:
        _write_lines(target, filtered)
    return removed

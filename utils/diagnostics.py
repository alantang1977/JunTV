import configparser
import io
import os
import time
import zipfile

import utils.constants as constants
from utils.config import config, resource_path


def _archive_path(prefix: str) -> str:
    target_dir = os.path.join(constants.output_dir, "diagnostics")
    os.makedirs(target_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(target_dir, f"{prefix}-{timestamp}.zip")
    suffix = 2
    while os.path.exists(path):
        path = os.path.join(target_dir, f"{prefix}-{timestamp}-{suffix}.zip")
        suffix += 1
    return path


def export_logs() -> str:
    path = _archive_path("iptv-api-logs")
    log_dir = os.path.join(constants.output_dir, "log")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if os.path.isdir(log_dir):
            for root, _, files in os.walk(log_dir):
                for filename in sorted(files):
                    source = os.path.join(root, filename)
                    archive.write(source, os.path.relpath(source, constants.output_dir))
    return os.path.abspath(path)


def export_diagnostics() -> str:
    path = _archive_path("iptv-api-diagnostics")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in [
            (resource_path("version.json"), "version.json"),
            (resource_path("config/config.ini"), "config/default_config.ini"),
            (constants.log_path, "logs/runtime.log"),
            (constants.result_log_path, "logs/result.log"),
            (constants.speed_test_log_path, "logs/speed_test.log"),
            (constants.statistic_log_path, "logs/statistic.log"),
            (constants.unmatch_log_path, "logs/unmatch.log"),
            (constants.runtime_jsonl_path, "logs/runtime.jsonl"),
            (constants.result_jsonl_path, "logs/result.jsonl"),
            (constants.speed_test_jsonl_path, "logs/speed_test.jsonl"),
            (constants.statistic_jsonl_path, "logs/statistic.jsonl"),
            (constants.unmatch_jsonl_path, "logs/unmatch.jsonl"),
            (constants.run_state_path, "data/run_state.json"),
        ]:
            if os.path.exists(source):
                archive.write(source, name)
        redacted = configparser.ConfigParser()
        for section in config.config.sections():
            redacted.add_section(section)
            for key, value in config.config.items(section):
                sensitive = any(part in key.lower() for part in ("password", "secret", "token", "proxy", "header"))
                redacted.set(section, key, "***" if sensitive and value else value)
        buffer = io.StringIO()
        redacted.write(buffer)
        archive.writestr("config/effective_redacted.ini", buffer.getvalue())
    return os.path.abspath(path)

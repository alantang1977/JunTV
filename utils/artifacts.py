from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Callable

from utils.i18n import t
from utils.reporting import sanitize_fields


class ArtifactWriter:
    """Write a latest-run human-readable artifact and a JSONL companion."""

    def __init__(
        self,
        text_path: str,
        jsonl_path: str,
        formatter: Callable[[dict[str, Any]], str],
        *,
        limit: int = 10000,
    ):
        self.text_path = text_path
        self.jsonl_path = jsonl_path
        self.formatter = formatter
        self.limit = max(0, int(limit))
        self.count = 0
        self.dropped = 0
        self._lock = threading.RLock()
        for path in (text_path, jsonl_path):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._text = open(text_path, "w", encoding="utf-8")
        self._jsonl = open(jsonl_path, "w", encoding="utf-8")

    def write(self, record: dict[str, Any]):
        with self._lock:
            if self.count >= self.limit:
                self.dropped += 1
                return
            payload = sanitize_fields({
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                **record,
            })
            self._text.write(self.formatter(payload).rstrip("\n") + "\n")
            self._jsonl.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            self.count += 1

    def close(self):
        with self._lock:
            if self.dropped:
                notice = {
                    "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "event": "artifact.truncated",
                    "dropped": self.dropped,
                    "limit": self.limit,
                }
                self._text.write(
                    t("log.artifact_truncated").format(dropped=self.dropped, limit=self.limit) + "\n"
                )
                self._jsonl.write(json.dumps(notice, ensure_ascii=False) + "\n")
            for file in (self._text, self._jsonl):
                file.flush()
                file.close()

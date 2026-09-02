import hashlib
import json


def stable_result_id(url: str, headers: dict | None = None) -> str:
    normalized_headers = {
        str(key).strip().lower(): str(value).strip()
        for key, value in sorted((headers or {}).items(), key=lambda item: str(item[0]).lower())
        if value is not None
    }
    payload = json.dumps(
        {"url": (url or "").strip(), "headers": normalized_headers},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def stable_channel_id(category: str, name: str) -> str:
    payload = f"{(category or '').strip()}\x1f{(name or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

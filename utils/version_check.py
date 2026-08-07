import re
import threading
from typing import Callable

import requests


REPOSITORY_URL = "https://github.com/Guovin/iptv-api"
LATEST_RELEASE_API = "https://api.github.com/repos/Guovin/iptv-api/releases/latest"
VERSION_CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", value or "")]
    return tuple(numbers or [0])


def parse_release(data: dict, current_version: str) -> dict:
    latest = str(data.get("tag_name") or data.get("name") or "").lstrip("v")
    return {
        "current": current_version,
        "latest": latest,
        "newer": version_tuple(latest) > version_tuple(current_version),
        "release_url": data.get("html_url") or REPOSITORY_URL + "/releases/latest",
        "assets": data.get("assets") or [],
    }


def check_latest_release(current_version: str, timeout: float = 4.0) -> dict:
    response = requests.get(
        LATEST_RELEASE_API,
        headers={"User-Agent": "IPTV-API"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_release(response.json(), current_version)


def log_new_version_if_available(current_version: str, checker: Callable = check_latest_release, reporter=None):
    try:
        result = checker(current_version)
    except Exception:
        return None
    if result.get("newer"):
        from utils.i18n import t
        message = t("msg.new_version_available_log").format(
            current=result.get("current") or current_version,
            latest=result.get("latest") or "",
            url=result.get("release_url") or REPOSITORY_URL + "/releases/latest",
        )
        if reporter:
            reporter.info(
                "version.available",
                message,
                current=result.get("current") or current_version,
                latest=result.get("latest") or "",
                url=result.get("release_url") or REPOSITORY_URL + "/releases/latest",
            )
        else:
            print(message, flush=True)
    return result


def start_version_log_monitor(
        current_version: str,
        interval: float = VERSION_CHECK_INTERVAL_SECONDS,
        checker: Callable = check_latest_release,
        reporter=None,
) -> threading.Event:
    stop_event = threading.Event()

    def monitor():
        while not stop_event.wait(interval):
            log_new_version_if_available(current_version, checker=checker, reporter=reporter)

    threading.Thread(target=monitor, name="version-check-monitor", daemon=True).start()
    return stop_event

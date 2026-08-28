import os
import shutil
import sys


_MACOS_SEARCH_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


def _usable_executable(path: str | None) -> str | None:
    if not path:
        return None
    expanded = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        return expanded
    return None


def _resolve_executable(name: str, env_var: str, companion_env_var: str) -> str | None:
    configured = _usable_executable(os.environ.get(env_var))
    if configured:
        return configured

    discovered = shutil.which(name)
    if discovered:
        return os.path.abspath(discovered)

    companion = os.environ.get(companion_env_var)
    if companion:
        sibling = shutil.which(name, path=os.path.dirname(companion))
        if sibling:
            return os.path.abspath(sibling)

    if sys.platform == "darwin":
        for directory in _MACOS_SEARCH_DIRS:
            candidate = _usable_executable(os.path.join(directory, name))
            if candidate:
                return candidate

    return None


def resolve_ffmpeg_executable() -> str | None:
    return _resolve_executable(
        "ffmpeg",
        "IPTV_API_FFMPEG_PATH",
        "IPTV_API_FFPROBE_PATH",
    )


def resolve_ffprobe_executable() -> str | None:
    return _resolve_executable(
        "ffprobe",
        "IPTV_API_FFPROBE_PATH",
        "IPTV_API_FFMPEG_PATH",
    )

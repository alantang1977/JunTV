import subprocess
import sys


def no_window_process_kwargs() -> dict[str, int]:
    if sys.platform != "win32":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }

import os
import sys
from pathlib import Path


def bundled_resource_path(relative_path: str) -> str:
    if os.path.isabs(relative_path):
        return relative_path
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return os.path.join(bundle_root, relative_path)
    return str(Path(__file__).resolve().parents[1] / relative_path)


def resource_path(relative_path: str, persistent: bool = False) -> str:
    if os.path.isabs(relative_path):
        return relative_path
    working_path = os.path.abspath(relative_path)
    if persistent or os.path.exists(working_path):
        return working_path
    return bundled_resource_path(relative_path)

"""Standalone update helper. It must never run from the bundle it replaces."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


class UpdateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_process(pid: int, timeout: float = 45) -> None:
    """Wait for the old GUI process without relying on optional dependencies."""
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return
        try:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, remaining)
            if result == 0x00000102:
                raise UpdateError("等待旧版本退出超时")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return

    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(0.1)
    raise UpdateError("等待旧版本退出超时")


def _safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            item_path = destination / item.filename
            resolved = item_path.resolve()
            if destination.resolve() not in (resolved, *resolved.parents):
                raise UpdateError("更新包包含非法路径")
            mode = item.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise UpdateError("更新包不能包含符号链接")
            if item.is_dir():
                resolved.mkdir(parents=True, exist_ok=True)
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, resolved.open("wb") as output:
                shutil.copyfileobj(source, output)
            if mode:
                resolved.chmod(mode & 0o777)


def extracted_application_root(staging_dir: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    entries = [entry for entry in staging_dir.iterdir() if not entry.name.startswith("__MACOSX")]
    if len(entries) != 1 or not entries[0].is_dir():
        raise UpdateError("更新包必须只包含一个应用目录")
    root = entries[0]
    if platform_name == "darwin" and root.suffix.lower() != ".app":
        raise UpdateError("更新包不包含 macOS 应用")
    return root


def application_entry(root: Path, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        candidates = [path for path in (root / "Contents" / "MacOS").iterdir() if path.is_file() and path.name != "updater"]
    else:
        candidates = [path for path in root.glob("*.exe") if path.name.lower() != "updater.exe"]
    if len(candidates) != 1:
        raise UpdateError("无法定位新版本启动程序")
    return candidates[0]


def _replace(target: Path, payload: Path, backup: Path) -> None:
    if backup.exists():
        shutil.rmtree(backup)
    os.replace(target, backup)
    try:
        os.replace(payload, target)
    except Exception:
        os.replace(backup, target)
        raise


def _restore(target: Path, backup: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    if backup.exists():
        os.replace(backup, target)


def install_update(pid: int, archive: Path, target: Path, expected_sha256: str) -> None:
    expected_sha256 = expected_sha256.lower()
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise UpdateError("无效的 SHA-256 校验值")
    if sha256_file(archive).lower() != expected_sha256:
        raise UpdateError("更新包 SHA-256 校验失败")
    if not target.exists() or not target.parent.is_dir():
        raise UpdateError("当前应用目录不存在")

    wait_for_process(pid)
    work_dir = Path(tempfile.mkdtemp(prefix=".iptv-api-update-", dir=target.parent))
    backup = target.with_name(f".{target.name}.previous")
    marker = target.parent / f".{target.name}.update-health.json"
    try:
        _safe_extract(archive, work_dir)
        payload = extracted_application_root(work_dir)
        application_entry(payload)
        marker.unlink(missing_ok=True)
        _replace(target, payload, backup)
        entry = application_entry(target)
        process = subprocess.Popen([str(entry)], cwd=str(target), env={
            **os.environ,
            "IPTV_API_UPDATE_HEALTH_FILE": str(marker),
        })
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if marker.is_file():
                backup_exists = backup.exists()
                if backup_exists:
                    shutil.rmtree(backup)
                return
            if process.poll() is not None:
                break
            time.sleep(0.2)
        if process.poll() is None:
            process.terminate()
        raise UpdateError("新版本未能启动，已恢复旧版本")
    except Exception:
        if backup.exists():
            _restore(target, backup)
            try:
                subprocess.Popen([str(application_entry(target))], cwd=str(target))
            except OSError:
                pass
        raise
    finally:
        marker.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        install_update(args.pid, args.archive.resolve(), args.target.resolve(), args.sha256)
    except Exception as exc:
        error_file = args.archive.with_suffix(args.archive.suffix + ".update-error.txt")
        error_file.write_text(str(exc), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

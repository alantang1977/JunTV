"""Launch the external helper that replaces a frozen desktop build."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths


class UpdateInstallError(RuntimeError):
    """Raised when a downloaded update cannot safely be installed."""


def application_root(executable: str | None = None, platform_name: str | None = None) -> Path:
    """Return the replaceable application directory for a frozen build."""
    executable_path = Path(executable or sys.executable).resolve()
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        for parent in executable_path.parents:
            if parent.suffix.lower() == ".app":
                return parent
        raise UpdateInstallError("未找到 macOS 应用包")
    return executable_path.parent


def bundled_updater_path(executable: str | None = None, platform_name: str | None = None) -> Path:
    executable_path = Path(executable or sys.executable).resolve()
    platform_name = platform_name or sys.platform
    name = "updater.exe" if platform_name == "win32" else "updater"
    return executable_path.parent / name


def _writable_directory(path: Path) -> bool:
    try:
        probe = path / ".iptv-api-update-write-test"
        with open(probe, "xb"):
            pass
        probe.unlink()
        return True
    except OSError:
        return False


def launch_update(archive: str, expected_sha256: str) -> None:
    """Copy the helper outside the bundle and ask it to install after exit."""
    if not getattr(sys, "frozen", False):
        raise UpdateInstallError("仅已安装的桌面应用支持自动安装更新")

    archive_path = Path(archive).resolve()
    if not archive_path.is_file():
        raise UpdateInstallError("更新包不存在")
    if not expected_sha256:
        raise UpdateInstallError("更新包缺少 SHA-256 校验信息，不能自动安装")

    target = application_root()
    if not _writable_directory(target.parent):
        raise UpdateInstallError("应用安装位置不可写；请移至用户应用目录后重试")

    source = bundled_updater_path()
    if not source.is_file():
        raise UpdateInstallError("更新器不可用；请重新下载完整安装包")

    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    helper_dir = data_dir / "updater"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / source.name
    shutil.copy2(source, helper)
    if sys.platform != "win32":
        helper.chmod(helper.stat().st_mode | 0o111)

    command = [
        str(helper),
        "--pid", str(os.getpid()),
        "--archive", str(archive_path),
        "--target", str(target),
        "--sha256", expected_sha256,
    ]
    try:
        subprocess.Popen(command, close_fds=True, start_new_session=True)
    except OSError as exc:
        raise UpdateInstallError(str(exc)) from exc

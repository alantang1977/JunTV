"""Select and prepare the desktop application's persistent data directory."""

import os
import sys
import tempfile
from pathlib import Path


DATA_DIRECTORY_ENV = "IPTV_API_DATA_DIR"
DATA_DIRECTORY_OPTION = "--data-dir"
DATA_DIRECTORY_SETTING = "runtime/data_directory"


class RuntimeDirectoryError(RuntimeError):
    """Raised when an explicitly selected data directory cannot be used."""


def _take_directory_option(argv: list[str]) -> str | None:
    for index, value in enumerate(tuple(argv)):
        if value == DATA_DIRECTORY_OPTION:
            if index + 1 >= len(argv) or not argv[index + 1].strip():
                raise RuntimeDirectoryError(f"{DATA_DIRECTORY_OPTION} 需要指定目录")
            directory = argv[index + 1]
            del argv[index:index + 2]
            return directory
        if value.startswith(f"{DATA_DIRECTORY_OPTION}="):
            directory = value.partition("=")[2].strip()
            if not directory:
                raise RuntimeDirectoryError(f"{DATA_DIRECTORY_OPTION} 需要指定目录")
            del argv[index]
            return directory
    return None


def _writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".iptv-api-write-", delete=True):
            pass
        return True
    except OSError:
        return False


def validate_runtime_directory(directory: str | os.PathLike[str]) -> Path:
    path = Path(directory).expanduser().resolve()
    if not _writable_directory(path):
        raise RuntimeDirectoryError(f"数据目录不可写：{path}")
    return path


def default_runtime_directory(
    *,
    fallback_directory: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    executable: str | os.PathLike[str] | None = None,
    prefer_executable_directory: bool = True,
) -> Path:
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return Path.cwd().resolve()
    if frozen and prefer_executable_directory:
        directory = Path(executable or sys.executable).resolve().parent
        if _writable_directory(directory):
            return directory
    if fallback_directory is not None:
        return validate_runtime_directory(fallback_directory)
    raise RuntimeDirectoryError("未找到可用的数据目录")


def prepare_runtime_directory(
    argv: list[str],
    *,
    fallback_directory: str | os.PathLike[str] | None = None,
    environ: dict[str, str] | None = None,
    frozen: bool | None = None,
    executable: str | os.PathLike[str] | None = None,
    prefer_executable_directory: bool = True,
    saved_directory: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Set the working directory for desktop config and output data.

    An explicit command-line option or environment variable takes precedence.
    Frozen applications otherwise prefer their executable directory and use the
    supplied system data-directory fallback only when that location is locked.
    """
    environ = os.environ if environ is None else environ
    option_directory = _take_directory_option(argv)
    requested_directory = option_directory or environ.get(DATA_DIRECTORY_ENV, "").strip()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    directory_validated = False

    if requested_directory:
        directory = Path(requested_directory).expanduser().resolve()
        explicit = True
    elif saved_directory:
        directory = Path(saved_directory).expanduser().resolve()
        explicit = False
    elif frozen:
        directory = default_runtime_directory(
            fallback_directory=fallback_directory,
            frozen=frozen,
            executable=executable,
            prefer_executable_directory=prefer_executable_directory,
        )
        explicit = False
        directory_validated = True
    else:
        return None

    if not directory_validated and not _writable_directory(directory):
        if explicit or fallback_directory is None:
            raise RuntimeDirectoryError(f"数据目录不可写：{directory}")
        directory = Path(fallback_directory).expanduser().resolve()
        if not _writable_directory(directory):
            raise RuntimeDirectoryError(f"数据目录不可写：{directory}")

    os.chdir(directory)
    environ[DATA_DIRECTORY_ENV] = str(directory)
    return directory

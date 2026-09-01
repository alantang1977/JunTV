import glob
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from functools import lru_cache


def find_homebrew_executable() -> str | None:
    candidates = [
        shutil.which("brew"),
        "/opt/homebrew/bin/brew",
        "/usr/local/bin/brew",
    ]
    return next(
        (
            path
            for path in candidates
            if path and os.path.isfile(path) and os.access(path, os.X_OK)
        ),
        None,
    )


def find_nginx_executable() -> str | None:
    configured = os.getenv("IPTV_API_NGINX_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("nginx"),
        "/opt/homebrew/bin/nginx",
        "/usr/local/bin/nginx",
        "/opt/homebrew/opt/nginx-full/bin/nginx",
        "/usr/local/opt/nginx-full/bin/nginx",
    ]
    return next((path for path in candidates if path and os.path.isfile(path) and os.access(path, os.X_OK)), None)


def find_rtmp_module() -> str | None:
    configured = os.getenv("IPTV_API_NGINX_RTMP_MODULE", "").strip()
    patterns = [
        "/opt/homebrew/opt/*/modules/ngx_rtmp_module.so",
        "/usr/local/opt/*/modules/ngx_rtmp_module.so",
        "/opt/homebrew/lib/nginx/modules/ngx_rtmp_module.so",
        "/usr/local/lib/nginx/modules/ngx_rtmp_module.so",
    ]
    candidates = [configured]
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    return next((path for path in candidates if path and os.path.isfile(path)), None)


@lru_cache(maxsize=1)
def rtmp_runtime_status() -> dict:
    if sys.platform == "win32":
        return {"available": True, "executable": "", "module": "", "error_code": ""}
    if sys.platform != "darwin":
        return {"available": False, "executable": "", "module": "", "error_code": "unsupported_platform"}
    executable = find_nginx_executable()
    if not executable:
        return {"available": False, "executable": "", "module": "", "error_code": "nginx_missing"}
    module = find_rtmp_module()
    try:
        process = subprocess.run(
            [executable, "-V"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        build_info = f"{process.stdout}\n{process.stderr}".lower()
    except Exception:
        build_info = ""
    if not module and "rtmp" not in build_info:
        return {"available": False, "executable": executable, "module": "", "error_code": "rtmp_module_missing"}
    return {"available": True, "executable": executable, "module": module or "", "error_code": ""}


def _run_install_command(
    command: list[str],
    env: dict[str, str],
    on_output: Callable[[str], None] | None,
) -> tuple[int, str]:
    output = []
    heading = f"$ {shlex.join(command)}\n"
    output.append(heading)
    if on_output:
        on_output(heading)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    if process.stdout:
        for line in process.stdout:
            output.append(line)
            if on_output:
                on_output(line)
    return process.wait(), "".join(output).rstrip()


def install_rtmp_runtime(on_output: Callable[[str], None] | None = None) -> dict:
    if sys.platform != "darwin":
        return rtmp_runtime_status()
    brew = find_homebrew_executable()
    if not brew:
        return {"available": False, "error_code": "homebrew_missing", "output": ""}
    env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"}
    outputs = []
    try:
        returncode, output = _run_install_command(
            [brew, "tap", "denji/nginx"], env, on_output
        )
        outputs.append(output)
        if returncode != 0:
            return {"available": False, "error_code": "install_failed", "output": "\n".join(outputs)}
        returncode, output = _run_install_command(
            [brew, "trust", "denji/nginx"], env, on_output
        )
        outputs.append(output)
        if returncode != 0:
            return {"available": False, "error_code": "install_failed", "output": "\n".join(outputs)}
        returncode, output = _run_install_command(
            [brew, "list", "--versions", "nginx-full"], env, on_output
        )
        outputs.append(output)
        action = "reinstall" if returncode == 0 else "install"
        returncode, output = _run_install_command(
            [brew, action, "denji/nginx/nginx-full", "--with-rtmp-module"],
            env,
            on_output,
        )
        outputs.append(output)
    except OSError as error:
        if on_output:
            on_output(f"{error}\n")
        return {"available": False, "error_code": "install_failed", "output": str(error)}
    output = "\n".join(outputs)
    if returncode != 0:
        return {"available": False, "error_code": "install_failed", "output": output}
    rtmp_runtime_status.cache_clear()
    status = rtmp_runtime_status()
    return {**status, "output": output}

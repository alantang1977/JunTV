import asyncio
import os
import re
import tempfile
import time

from utils.ffmpeg.executable import resolve_ffmpeg_executable


def _parse_rate(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _parse_stream_metadata(output: str) -> dict:
    video_line = next(
        (line for line in (output or "").splitlines() if "Video:" in line),
        "",
    )
    audio_line = next(
        (line for line in (output or "").splitlines() if "Audio:" in line),
        "",
    )
    resolution_match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tbr)", video_line, re.IGNORECASE)
    video_codec_match = re.search(r"Video:\s*([^,\s(]+)", video_line, re.IGNORECASE)
    audio_codec_match = re.search(r"Audio:\s*([^,\s(]+)", audio_line, re.IGNORECASE)
    width = int(resolution_match.group(1)) if resolution_match else None
    height = int(resolution_match.group(2)) if resolution_match else None
    return {
        "resolution": f"{width}x{height}" if width and height else None,
        "width": width,
        "height": height,
        "fps": _parse_rate(fps_match.group(1) if fps_match else None),
        "video_codec": video_codec_match.group(1) if video_codec_match else None,
        "audio_codec": audio_codec_match.group(1) if audio_codec_match else None,
    }


async def capture_stream_screenshot(
    url: str,
    result_key: str,
    output_dir: str,
    headers: dict | None = None,
    timeout: int = 5,
    width: int = 640,
) -> dict:
    attempted_at = time.time()
    executable = resolve_ffmpeg_executable()
    filename = f"{result_key}.jpg"
    final_path = os.path.join(output_dir, filename)
    result = {
        "result_key": result_key,
        "filename": filename,
        "status": "failed",
        "attempted_at": attempted_at,
        "captured_at": None,
        "error": None,
    }
    if not executable:
        if os.path.exists(final_path):
            try:
                os.unlink(final_path)
            except OSError:
                pass
        result.update(status="unavailable", error="ffmpeg_missing")
        return result

    os.makedirs(output_dir, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{result_key}.",
        suffix=".jpg",
        dir=output_dir,
    )
    os.close(descriptor)
    header_text = "".join(
        f"{key}: {value}\r\n" for key, value in (headers or {}).items()
    )
    args = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-threads",
        "1",
    ]
    if header_text and url.lower().startswith(("http://", "https://")):
        args += ["-headers", header_text]
    args += [
        "-i",
        url,
        "-ss",
        "1",
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-vf",
        f"scale=min({max(160, int(width))}\\,iw):-2",
        "-q:v",
        "4",
        "-y",
        temporary_path,
    ]

    process = None
    succeeded = False
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=max(1, int(timeout)))
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            result["error"] = "timeout"
            return result

        output = stderr.decode("utf-8", errors="replace")
        metadata = _parse_stream_metadata(output)
        if process.returncode != 0 or not os.path.isfile(temporary_path):
            result["error"] = "decode_failed"
            return {**result, **metadata}
        if os.path.getsize(temporary_path) < 1024:
            result["error"] = "no_frame"
            return {**result, **metadata}

        os.replace(temporary_path, final_path)
        succeeded = True
        return {
            **result,
            **metadata,
            "status": "success",
            "captured_at": time.time(),
            "error": None,
        }
    except asyncio.CancelledError:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise
    except Exception:
        result["error"] = "capture_error"
        return result
    finally:
        if os.path.exists(temporary_path):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        if not succeeded and os.path.exists(final_path):
            try:
                os.unlink(final_path)
            except OSError:
                pass

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from time import monotonic
from typing import Any, Callable, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import utils.constants as constants

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table
except ImportError:  # pragma: no cover - fallback for partially upgraded installs
    Console = None
    Progress = None
    Table = None


EventCallback = Callable[[dict[str, Any]], None]
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:token|auth|authorization|secret|signature|password|passwd|expires?|(?:^|[_-])key(?:$|[_-]))",
    re.IGNORECASE,
)
_URL_IN_TEXT = re.compile(r"(?:https?|rtmp|rtsp)://[^\s，。；）)\]}]+", re.IGNORECASE)
_LEADING_DECORATIONS = (
    "⚡️", "⚡", "⚙️", "⚙", "🚧", "✅", "⚠️", "⚠", "🕒", "🥳", "🔍",
    "⛔", "🆗", "📊", "🆙", "🌐", "🚀", "❌", "🫥", "⬆️", "⬆", "📢",
)


def redact_url(value: str | None) -> str | None:
    """Hide credentials and sensitive query values while preserving diagnostics."""
    if not value or not isinstance(value, str):
        return value
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{hostname}{port}"
        query = urlencode(
            [
                (key, "***" if _SENSITIVE_QUERY_KEY.search(key) else item_value)
                for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except (TypeError, ValueError):
        return value


def sanitize_fields(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): sanitize_fields(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_fields(item, key) for item in value]
    if isinstance(value, str) and ("url" in key.lower() or value.startswith(("http://", "https://", "rtmp://", "rtsp://"))):
        return redact_url(value)
    return value


def redact_urls_in_text(value: str) -> str:
    return _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)) or "", value)


def normalize_message(value: str) -> str:
    message = redact_urls_in_text(value).strip()
    changed = True
    while changed:
        changed = False
        for decoration in _LEADING_DECORATIONS:
            if message.startswith(decoration):
                message = message[len(decoration):].lstrip()
                changed = True
                break
    return message


@dataclass(slots=True)
class ReportEvent:
    event: str
    message: str
    level: str = "INFO"
    run_id: str | int | None = None
    phase: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    data: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        result = asdict(self)
        result["data"] = sanitize_fields(result["data"])
        return result


class _RuntimeFileSink:
    def __init__(self, text_path: str, jsonl_path: str):
        os.makedirs(os.path.dirname(text_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._jsonl_path = jsonl_path
        os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)
        self._logger = logging.getLogger(f"iptv.runtime.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(
            text_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.handlers.clear()
        self._logger.addHandler(handler)
        self._json_logger = logging.getLogger(f"iptv.runtime.json.{id(self)}")
        self._json_logger.setLevel(logging.INFO)
        self._json_logger.propagate = False
        json_handler = RotatingFileHandler(
            jsonl_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
            delay=True,
        )
        json_handler.setFormatter(logging.Formatter("%(message)s"))
        self._json_logger.handlers.clear()
        self._json_logger.addHandler(json_handler)

    def emit(self, event: ReportEvent):
        payload = event.payload()
        context = []
        if event.run_id is not None:
            context.append(f"run={event.run_id}")
        if event.phase:
            context.append(f"phase={event.phase}")
        context.append(f"event={event.event}")
        suffix = f" [{' '.join(context)}]" if context else ""
        line = f"{event.timestamp} {event.level:<7}{suffix} {event.message}"
        with self._lock:
            self._logger.log(getattr(logging, event.level, logging.INFO), line)
            self._json_logger.info(json.dumps(payload, ensure_ascii=False, default=str))

    def close(self):
        with self._lock:
            for logger in (self._logger, self._json_logger):
                for handler in logger.handlers[:]:
                    handler.flush()
                    handler.close()
                    logger.removeHandler(handler)


class Reporter:
    """One reporting facade for terminal, runtime files, and desktop callbacks."""

    def __init__(
        self,
        run_id: str | int | None = None,
        *,
        event_callback: EventCallback | None = None,
        stream: TextIO | None = None,
        enable_console: bool = True,
        enable_runtime_file: bool = True,
    ):
        self.run_id = run_id
        self.event_callback = event_callback
        self.stream = stream or sys.stdout
        self.enable_console = enable_console
        self._lock = threading.RLock()
        self._progress = None
        self._tasks: dict[str, int] = {}
        self._plain_progress: dict[str, dict[str, Any]] = {}
        self._rich_console = None
        self._interactive = bool(
            enable_console
            and hasattr(self.stream, "isatty")
            and self.stream.isatty()
            and os.getenv("TERM", "").lower() not in {"dumb", "unknown"}
            and not os.getenv("GITHUB_ACTIONS")
            and not os.getenv("IPTV_API_PLAIN_OUTPUT")
        )
        if self._interactive and Console is not None:
            self._rich_console = Console(file=self.stream, color_system="auto", markup=False)
        self._file_sink = None
        if enable_runtime_file:
            try:
                self._file_sink = _RuntimeFileSink(constants.log_path, constants.runtime_jsonl_path)
            except OSError:
                self._file_sink = None

    def bind_run(self, run_id: str | int | None):
        self.run_id = run_id

    def emit(self, level: str, event: str, message: str, *, phase: str | None = None, **data):
        report_event = ReportEvent(
            event=event,
            message=normalize_message(str(message)),
            level=level.upper(),
            run_id=self.run_id,
            phase=phase,
            data=data,
        )
        with self._lock:
            if self._file_sink:
                try:
                    self._file_sink.emit(report_event)
                except (OSError, ValueError):
                    pass
            if self.event_callback:
                try:
                    self.event_callback(report_event.payload())
                except Exception:
                    pass
            if self.enable_console:
                self._render_console(report_event)

    def info(self, event: str, message: str, *, phase: str | None = None, **data):
        self.emit("INFO", event, message, phase=phase, **data)

    def warning(self, event: str, message: str, *, phase: str | None = None, **data):
        self.emit("WARNING", event, message, phase=phase, **data)

    def error(self, event: str, message: str, *, phase: str | None = None, **data):
        self.emit("ERROR", event, message, phase=phase, **data)

    def _render_console(self, event: ReportEvent):
        prefix = {
            "INFO": "•",
            "WARNING": "!",
            "ERROR": "×",
            "CRITICAL": "×",
        }.get(event.level, "•")
        style = {
            "INFO": "cyan",
            "WARNING": "yellow",
            "ERROR": "bold red",
            "CRITICAL": "bold red",
        }.get(event.level)
        if self._rich_console:
            self._rich_console.print(f"{prefix} {event.message}", style=style)
        else:
            print(f"{prefix} {event.message}", file=self.stream, flush=True)

    def _ensure_progress(self):
        if self._progress is not None or not self._rich_console or Progress is None:
            return
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", style="cyan"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TextColumn("{task.completed:.0f}/{task.total:.0f}"),
            TimeRemainingColumn(),
            TextColumn("{task.fields[status]}", style="dim"),
            console=self._rich_console,
            refresh_per_second=5,
            transient=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._progress.start()

    def start_progress(self, key: str, description: str, total: int, *, phase: str | None = None):
        total = max(0, int(total))
        with self._lock:
            if self._rich_console:
                self._ensure_progress()
                self._tasks[key] = self._progress.add_task(description, total=total, status="", phase=phase)
                self.info(
                    "progress.started",
                    f"{description}  0/{total}",
                    phase=phase,
                    progress_key=key,
                    current=0,
                    total=total,
                )
            else:
                self._plain_progress[key] = {
                    "description": description,
                    "total": total,
                    "completed": 0,
                    "last_percent": -10,
                    "last_emit": monotonic(),
                    "phase": phase,
                }
                self.info(
                    "progress.started",
                    f"{description}  0/{total}",
                    phase=phase,
                    progress_key=key,
                    current=0,
                    total=total,
                )

    def update_progress(
        self,
        key: str,
        *,
        advance: int = 0,
        completed: int | None = None,
        status: str = "",
        **metrics,
    ):
        with self._lock:
            if key in self._tasks and self._progress:
                task_id = self._tasks[key]
                kwargs: dict[str, Any] = {"status": status}
                if completed is not None:
                    kwargs["completed"] = completed
                elif advance:
                    kwargs["advance"] = advance
                self._progress.update(task_id, **kwargs)
                return
            state = self._plain_progress.get(key)
            if not state:
                return
            if completed is not None:
                state["completed"] = min(max(0, int(completed)), state["total"])
            else:
                state["completed"] = min(state["total"], state["completed"] + max(0, int(advance)))
            percent = int(state["completed"] / state["total"] * 100) if state["total"] else 100
            now = monotonic()
            if percent < 100 and percent - state["last_percent"] < 10 and now - state["last_emit"] < 30:
                return
            state["last_percent"] = percent
            state["last_emit"] = now
            metric_text = f"  {status}" if status else ""
            self.info(
                "progress.updated",
                f"{state['description']}  {state['completed']}/{state['total']} ({percent}%){metric_text}",
                phase=state["phase"],
                progress_key=key,
                current=state["completed"],
                total=state["total"],
                **metrics,
            )

    def finish_progress(
        self,
        key: str,
        *,
        status: str = "",
        message: str | None = None,
        remove: bool = False,
        **metrics,
    ):
        with self._lock:
            if key in self._tasks and self._progress:
                task_id = self._tasks.pop(key)
                task = next(task for task in self._progress.tasks if task.id == task_id)
                self._progress.update(task_id, completed=task.total, status=status)
                final_message = message or f"{task.description}  {task.total:.0f}/{task.total:.0f}"
                if status and not message:
                    final_message += f"  {status}"
                self.info(
                    "progress.finished",
                    final_message,
                    phase=task.fields.get("phase"),
                    progress_key=key,
                    current=task.total,
                    total=task.total,
                    status=status,
                    **metrics,
                )
                if remove:
                    self._progress.remove_task(task_id)
            state = self._plain_progress.pop(key, None)
            if state:
                final_message = message or f"{state['description']}  {state['total']}/{state['total']}"
                if status and not message:
                    final_message += f"  {status}"
                self.info(
                    "progress.finished",
                    final_message,
                    phase=state["phase"],
                    progress_key=key,
                    current=state["total"],
                    total=state["total"],
                    status=status,
                    **metrics,
                )

    def summary(
        self,
        title: str,
        metrics: list[tuple[str, Any]],
        *,
        outputs: list[tuple[str, str]] | None = None,
        phase: str = "complete",
    ):
        data = {
            "metrics": {key: value for key, value in metrics},
            "outputs": {key: value for key, value in (outputs or [])},
        }
        self.info("run.summary", title, phase=phase, **data)
        if not self.enable_console:
            return
        if not self._rich_console or Table is None:
            for label, value in metrics:
                print(f"  {label:<12} {value}", file=self.stream)
            for label, value in outputs or []:
                print(f"  {label:<12} {value}", file=self.stream)
            self.stream.flush()
            return
        table = Table(show_header=False, box=None, padding=(0, 2), title=None)
        table.add_column(style="dim")
        table.add_column(style="bold")
        for label, value in metrics:
            table.add_row(str(label), str(value))
        if outputs:
            table.add_section()
            for label, value in outputs:
                table.add_row(str(label), str(value), style="cyan")
        self._rich_console.print(table)

    def stop_progress(self):
        with self._lock:
            if self._progress is not None:
                self._progress.stop()
                self._progress = None
            self._tasks.clear()
            self._plain_progress.clear()

    def close(self):
        with self._lock:
            self.stop_progress()
            if self._file_sink:
                self._file_sink.close()
                self._file_sink = None


def format_metric(value: Any, fallback: str = "—") -> str:
    return fallback if value is None or value == "" else str(value)

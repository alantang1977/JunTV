import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from utils.channel_quality import is_channel_result_valid
from utils.config import config
from utils.config import resource_path
import utils.constants as constants
from utils.db import get_db_connection, return_db_connection
from utils.identity import stable_channel_id, stable_result_id
from utils.i18n import t


_LOCK = threading.Lock()


def ensure_channel_repository(db_path: str) -> None:
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL,
                    config_hash TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS channels (
                    channel_key TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    total_results INTEGER NOT NULL DEFAULT 0,
                    untested_results INTEGER NOT NULL DEFAULT 0,
                    selection_mode TEXT NOT NULL DEFAULT 'auto',
                    valid_results INTEGER NOT NULL DEFAULT 0,
                    selected_results INTEGER NOT NULL DEFAULT 0,
                    best_speed REAL,
                    min_delay REAL,
                    max_resolution TEXT,
                    health TEXT NOT NULL DEFAULT 'unknown',
                    logo TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_channels_category ON channels(category, name);
                CREATE TABLE IF NOT EXISTS channel_results (
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    host TEXT,
                    headers TEXT,
                    origin TEXT,
                    ipv_type TEXT,
                    location TEXT,
                    isp TEXT,
                    speed REAL,
                    delay REAL,
                    resolution TEXT,
                    fps REAL,
                    video_codec TEXT,
                    audio_codec TEXT,
                    supply INTEGER NOT NULL DEFAULT 0,
                    valid INTEGER NOT NULL DEFAULT 0,
                    test_status TEXT,
                    error_type TEXT,
                    selected_rank INTEGER,
                    tested_at REAL,
                    last_seen_at REAL NOT NULL,
                    extra_data TEXT,
                    PRIMARY KEY(channel_key, result_key),
                    FOREIGN KEY(channel_key) REFERENCES channels(channel_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_results_result_key ON channel_results(result_key);
                CREATE TABLE IF NOT EXISTS output_snapshots (
                    run_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    output_rank INTEGER NOT NULL,
                    exported_at REAL NOT NULL,
                    PRIMARY KEY(run_id, channel_key, result_key),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                    FOREIGN KEY(channel_key) REFERENCES channels(channel_key) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_output_snapshots_run ON output_snapshots(run_id, channel_key, output_rank);
                CREATE TABLE IF NOT EXISTS channel_selection (
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    selection_rank INTEGER NOT NULL,
                    selection_state TEXT NOT NULL DEFAULT 'included',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(channel_key, result_key),
                    FOREIGN KEY(channel_key) REFERENCES channels(channel_key) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS candidate_history (
                    run_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    origin TEXT,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    PRIMARY KEY(run_id, channel_key, result_key),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_history_key
                    ON candidate_history(channel_key, result_key, last_seen_at);
                CREATE TABLE IF NOT EXISTS candidate_pool (
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    origin TEXT,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_run_id TEXT,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(channel_key, result_key)
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_pool_channel
                    ON candidate_pool(channel_key, last_seen_at);
                CREATE TABLE IF NOT EXISTS candidate_measurements (
                    run_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    speed REAL,
                    delay REAL,
                    resolution TEXT,
                    fps REAL,
                    video_codec TEXT,
                    audio_codec TEXT,
                    valid INTEGER NOT NULL DEFAULT 0,
                    test_status TEXT,
                    error_type TEXT,
                    tested_at REAL,
                    measured_at REAL NOT NULL,
                    PRIMARY KEY(run_id, channel_key, result_key),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_measurements_key
                    ON candidate_measurements(channel_key, result_key, measured_at);
                CREATE TABLE IF NOT EXISTS stream_samples (
                    sampled_at REAL NOT NULL,
                    result_key TEXT NOT NULL,
                    clients INTEGER NOT NULL DEFAULT 0,
                    bw_in REAL NOT NULL DEFAULT 0,
                    bw_out REAL NOT NULL DEFAULT 0,
                    bytes_in INTEGER NOT NULL DEFAULT 0,
                    bytes_out INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(sampled_at, result_key)
                );
                CREATE INDEX IF NOT EXISTS idx_stream_samples_time ON stream_samples(sampled_at);
                CREATE TABLE IF NOT EXISTS operation_history (
                    operation_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_key TEXT,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL,
                    message TEXT
                );
                CREATE TABLE IF NOT EXISTS stream_screenshots (
                    result_key TEXT PRIMARY KEY,
                    filename TEXT,
                    status TEXT NOT NULL,
                    captured_at REAL,
                    attempted_at REAL NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    error TEXT
                );
                PRAGMA user_version=2;
                """
            )
            # These names expose the four-layer model without breaking the
            # legacy ``channel_results`` table used by older integrations.
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS channel_candidates AS
                SELECT * FROM channel_results
                """
            )
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS candidate_selection AS
                SELECT channel_key, result_key,
                       selection_state AS state,
                       selection_rank AS manual_rank,
                       pinned,
                       updated_at
                FROM channel_selection
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(channels)")}
            if "logo" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN logo TEXT")
            if "untested_results" not in columns:
                conn.execute(
                    "ALTER TABLE channels ADD COLUMN untested_results INTEGER NOT NULL DEFAULT 0"
                )
            if "selection_mode" not in columns:
                conn.execute(
                    "ALTER TABLE channels ADD COLUMN selection_mode TEXT NOT NULL DEFAULT 'auto'"
                )
            result_columns = {row[1] for row in conn.execute("PRAGMA table_info(channel_results)")}
            if "test_status" not in result_columns:
                conn.execute("ALTER TABLE channel_results ADD COLUMN test_status TEXT")
            if "error_type" not in result_columns:
                conn.execute("ALTER TABLE channel_results ADD COLUMN error_type TEXT")
            selection_columns = {row[1] for row in conn.execute("PRAGMA table_info(channel_selection)")}
            if "selection_state" not in selection_columns:
                conn.execute(
                    "ALTER TABLE channel_selection ADD COLUMN selection_state TEXT NOT NULL DEFAULT 'included'"
                )
            if "pinned" not in selection_columns:
                conn.execute(
                    "ALTER TABLE channel_selection ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_results_channel_rank ON channel_results(channel_key, selected_rank)"
            )
            conn.commit()
        finally:
            return_db_connection(db_path, conn)


def _config_hash() -> str:
    values = {
        section: dict(config.config.items(section))
        for section in config.config.sections()
    }
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def start_run(db_path: str) -> str:
    ensure_channel_repository(db_path)
    run_id = uuid.uuid4().hex
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO runs(run_id, started_at, status, config_hash) VALUES (?, ?, ?, ?)",
            (run_id, time.time(), "running", _config_hash()),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)
    return run_id


def finish_run(db_path: str, run_id: str | None, status: str, error: str | None = None) -> None:
    if not run_id:
        return
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, error=? WHERE run_id=?",
            (time.time(), status, error, run_id),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)


def latest_successful_run(db_path: str) -> dict[str, Any] | None:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        return_db_connection(db_path, conn)


def _resolution_value(value: str | None) -> int:
    try:
        width, height = str(value).lower().replace("*", "x").split("x", 1)
        return int(width) * int(height)
    except (TypeError, ValueError):
        return 0


def _is_valid(item: dict) -> bool:
    return is_channel_result_valid(item, retain_special=True)


def _is_measured_valid(item: dict) -> bool:
    """A candidate is valid only after measurement, except retained sources."""
    return _is_valid(item) and (
        item.get("tested_at") is not None
        or item.get("origin") in {"whitelist", "hls"}
    )


def _refresh_channel_summary(conn, channel_key: str) -> None:
    rows = conn.execute(
        """
        SELECT speed, delay, resolution, valid, selected_rank, tested_at
        FROM channel_results
        WHERE channel_key=?
        """,
        (channel_key,),
    ).fetchall()
    valid_rows = [row for row in rows if row[3]]
    speeds = [
        row[0]
        for row in valid_rows
        if isinstance(row[0], (int, float)) and math.isfinite(row[0])
    ]
    delays = [
        row[1]
        for row in valid_rows
        if isinstance(row[1], (int, float)) and math.isfinite(row[1]) and row[1] >= 0
    ]
    resolutions = [row[2] for row in valid_rows if row[2]]
    health = "healthy" if len(valid_rows) >= 2 else "warning" if valid_rows else "offline"
    conn.execute(
        """
        UPDATE channels SET
            total_results=?, untested_results=?, valid_results=?, selected_results=?, best_speed=?,
            min_delay=?, max_resolution=?, health=?, updated_at=?
        WHERE channel_key=?
        """,
        (
            len(rows),
            sum(row[5] is None for row in rows),
            len(valid_rows),
            sum(row[4] is not None for row in rows),
            max(speeds, default=None),
            min(delays, default=None),
            max(resolutions, key=_resolution_value, default=None),
            health,
            time.time(),
            channel_key,
        ),
    )


def _merge_channel_items(base_items: list, tested_items: list, selected_items: list) -> list[dict]:
    merged: dict[str, dict] = {}
    tested_keys = set()
    selected_ranks = {}
    for item in base_items or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        key = stable_result_id(item["url"], item.get("headers"))
        merged[key] = {**item, "id": key}
    for item in tested_items or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        key = stable_result_id(item["url"], item.get("headers"))
        merged[key] = {**merged.get(key, {}), **item, "id": key}
        tested_keys.add(key)
    for rank, item in enumerate(selected_items or [], start=1):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        key = stable_result_id(item["url"], item.get("headers"))
        merged[key] = {**merged.get(key, {}), **item, "id": key}
        selected_ranks[key] = rank
    now = time.time()
    return [
        {
            **item,
            "result_key": key,
            "selected_rank": selected_ranks.get(key),
            "tested_at": now if key in tested_keys else None,
        }
        for key, item in merged.items()
    ]


def sync_channel_snapshot(
        db_path: str,
        base_data: dict,
        tested_data: dict | None = None,
        selected_data: dict | None = None,
        run_id: str | None = None,
) -> None:
    ensure_channel_repository(db_path)
    tested_data = tested_data or {}
    selected_data = selected_data or {}
    now = time.time()
    manual_selections = _load_manual_selections(db_path)
    existing_conn = get_db_connection(db_path)
    try:
        existing_updated_at = dict(existing_conn.execute("SELECT channel_key, updated_at FROM channels").fetchall())
    finally:
        return_db_connection(db_path, existing_conn)
    channel_rows = []
    result_rows = []
    channel_keys = set()

    for category, channel_map in (base_data or {}).items():
        for name, base_items in (channel_map or {}).items():
            channel_key = stable_channel_id(category, name)
            channel_keys.add(channel_key)
            selected_for_output = selected_data.get(category, {}).get(name, [])
            manual_keys = manual_selections.get(channel_key)
            if manual_keys is not None:
                pool = {}
                for item in (
                    list(base_items or [])
                    + list(tested_data.get(category, {}).get(name, []) or [])
                    + list(selected_for_output or [])
                ):
                    if isinstance(item, dict) and item.get("url"):
                        pool[stable_result_id(item["url"], item.get("headers"))] = item
                selected_for_output = [pool[key] for key in manual_keys if key in pool]
            if category != t("content.unmatch_channel"):
                selected_for_output = selected_for_output[: config.output_urls_limit]
            items = _merge_channel_items(
                base_items,
                tested_data.get(category, {}).get(name, []),
                selected_for_output,
            )
            valid_items = [item for item in items if _is_measured_valid(item)]
            tested_items = [item for item in items if item.get("tested_at")]
            selected_items = [item for item in items if item.get("selected_rank") is not None]
            speeds = [float(item["speed"]) for item in valid_items if isinstance(item.get("speed"), (int, float)) and not math.isinf(item["speed"])]
            delays = [float(item["delay"]) for item in valid_items if isinstance(item.get("delay"), (int, float)) and item["delay"] >= 0]
            resolutions = [item.get("resolution") for item in valid_items if item.get("resolution")]
            health = "healthy" if len(valid_items) >= 2 else "warning" if valid_items else "offline"
            if not tested_data.get(category, {}).get(name) and not selected_items:
                health = "unknown"
            tested_times = [item.get("tested_at") for item in items if item.get("tested_at")]
            updated_at = max(tested_times, default=existing_updated_at.get(channel_key, now))
            channel_rows.append((
                channel_key,
                category,
                name,
                len(items),
                max(0, len(items) - len(tested_items)),
                len(valid_items),
                len(selected_items),
                max(speeds, default=None),
                min(delays, default=None),
                max(resolutions, key=_resolution_value, default=None),
                health,
                updated_at,
            ))
            for item in items:
                extra_data = {
                    "date": item.get("date"),
                    "catchup": item.get("catchup"),
                    "tvg_logo": item.get("tvg_logo"),
                    "extra_info": item.get("extra_info"),
                }
                result_rows.append((
                    channel_key,
                    item["result_key"],
                    item.get("url"),
                    item.get("host"),
                    json.dumps(item.get("headers"), ensure_ascii=False, sort_keys=True) if item.get("headers") else None,
                    item.get("origin"),
                    item.get("ipv_type"),
                    item.get("location"),
                    item.get("isp"),
                    item.get("speed"),
                    item.get("delay"),
                    item.get("resolution"),
                    item.get("fps"),
                    item.get("video_codec"),
                    item.get("audio_codec"),
                    int(bool(item.get("supply"))),
                    int(_is_measured_valid(item)),
                    item.get("test_status") or ("valid" if _is_measured_valid(item) else None),
                    item.get("error_type"),
                    item.get("selected_rank"),
                    item.get("tested_at"),
                    now,
                    json.dumps(extra_data, ensure_ascii=False, sort_keys=True),
                ))

    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                """
                INSERT INTO channels(
                    channel_key, category, name, total_results, untested_results, selection_mode,
                    valid_results, selected_results, best_speed, min_delay, max_resolution, health, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_key) DO UPDATE SET
                    category=excluded.category, name=excluded.name, total_results=excluded.total_results,
                    untested_results=excluded.untested_results,
                    selection_mode=CASE WHEN channels.selection_mode='manual' THEN 'manual' ELSE excluded.selection_mode END,
                    valid_results=excluded.valid_results, selected_results=excluded.selected_results,
                    best_speed=excluded.best_speed, min_delay=excluded.min_delay,
                    max_resolution=excluded.max_resolution, health=excluded.health,
                    updated_at=excluded.updated_at
                """,
                [
                    (*row[:5], "manual" if stable_channel_id(row[1], row[2]) in manual_selections else "auto", *row[5:])
                    for row in channel_rows
                ],
            )
            conn.execute("DELETE FROM channel_results")
            conn.executemany(
                """
                INSERT INTO channel_results(
                    channel_key, result_key, url, host, headers, origin, ipv_type, location, isp,
                    speed, delay, resolution, fps, video_codec, audio_codec, supply, valid,
                    test_status, error_type, selected_rank, tested_at, last_seen_at, extra_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                result_rows,
            )
            if run_id:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO candidate_history(
                        run_id, channel_key, result_key, url, origin,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            row[0],
                            row[1],
                            row[2],
                            row[5],
                            now,
                            now,
                        )
                        for row in result_rows
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO candidate_pool(
                        channel_key, result_key, url, origin, first_seen_at,
                        last_seen_at, last_run_id, seen_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(channel_key, result_key) DO UPDATE SET
                        url=excluded.url,
                        origin=excluded.origin,
                        last_seen_at=excluded.last_seen_at,
                        last_run_id=excluded.last_run_id,
                        seen_count=candidate_pool.seen_count + 1
                    """,
                    [
                        (row[0], row[1], row[2], row[5], now, now, run_id)
                        for row in result_rows
                    ],
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO candidate_measurements(
                        run_id, channel_key, result_key, speed, delay, resolution,
                        fps, video_codec, audio_codec, valid, test_status, error_type,
                        tested_at, measured_at
                    )
                    SELECT ?, channel_key, result_key, speed, delay, resolution,
                           fps, video_codec, audio_codec, valid, test_status, error_type,
                           tested_at, ?
                    FROM channel_results
                    WHERE tested_at IS NOT NULL
                    """,
                    (run_id, now),
                )
            if channel_keys:
                placeholders = ",".join("?" for _ in channel_keys)
                conn.execute(f"DELETE FROM channels WHERE channel_key NOT IN ({placeholders})", tuple(channel_keys))
            else:
                conn.execute("DELETE FROM channels")
            if run_id:
                conn.execute("DELETE FROM output_snapshots WHERE run_id=?", (run_id,))
                conn.execute(
                    """
                    INSERT INTO output_snapshots(run_id, channel_key, result_key, output_rank, exported_at)
                    SELECT ?, channel_key, result_key, selected_rank, ?
                    FROM channel_results
                    WHERE selected_rank IS NOT NULL
                    """,
                    (run_id, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)


def list_categories(db_path: str, search: str = "") -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        where = ""
        params = []
        if search:
            where = "WHERE name LIKE ? OR category LIKE ?"
            params.extend([f"%{search}%", f"%{search}%"])
        rows = conn.execute(
            f"""
            SELECT category, COUNT(*) AS channel_count,
                   SUM(CASE WHEN health='healthy' THEN 1 ELSE 0 END) AS healthy_count,
                   SUM(CASE WHEN health='warning' THEN 1 ELSE 0 END) AS warning_count,
                   SUM(CASE WHEN health='offline' THEN 1 ELSE 0 END) AS offline_count,
                   SUM(valid_results) AS valid_results
            FROM channels {where} GROUP BY category ORDER BY category
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def list_channels(
    db_path: str,
    category: str | None = None,
    search: str = "",
    health: str | None = None,
) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params = []
        if category:
            clauses.append("category=?")
            params.append(category)
        if health:
            clauses.append("health=?")
            params.append(health)
        if search:
            clauses.append("(name LIKE ? OR category LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT channels.*,
                   (
                       SELECT channel_results.url
                       FROM channel_results
                       WHERE channel_results.channel_key=channels.channel_key
                         AND channel_results.url IS NOT NULL
                         AND channel_results.url != ''
                         AND channel_results.valid = 1
                       ORDER BY selected_rank IS NULL, selected_rank, valid DESC, speed DESC, delay ASC
                       LIMIT 1
                   ) AS best_url,
                   COALESCE(NULLIF(channels.logo, ''), (
                       SELECT json_extract(channel_results.extra_data, '$.tvg_logo')
                       FROM channel_results
                       WHERE channel_results.channel_key=channels.channel_key
                         AND json_extract(channel_results.extra_data, '$.tvg_logo') IS NOT NULL
                         AND json_extract(channel_results.extra_data, '$.tvg_logo') != ''
                       LIMIT 1
                   )) AS logo
            FROM channels {where} ORDER BY category, name
            """,
            params,
        ).fetchall()
        result = [dict(row) for row in rows]
        logo_root = resource_path(constants.channel_logo_path)
        for row in result:
            if row.get("logo"):
                continue
            local_logo = os.path.join(logo_root, f"{row['name']}.{config.logo_type}")
            if os.path.isfile(local_logo):
                row["logo"] = local_logo
            elif config.logo_url:
                row["logo"] = f"{config.logo_url.rstrip('/')}/{row['name']}.{config.logo_type}"
        return result
    finally:
        return_db_connection(db_path, conn)


def list_channel_results(db_path: str, channel_key: str) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT channel_results.*,
                   stream_screenshots.filename AS screenshot_filename,
                   stream_screenshots.status AS screenshot_status,
                   stream_screenshots.captured_at AS screenshot_captured_at,
                   stream_screenshots.attempted_at AS screenshot_attempted_at,
                   stream_screenshots.width AS screenshot_width,
                   stream_screenshots.height AS screenshot_height,
                   stream_screenshots.error AS screenshot_error
            FROM channel_results
            LEFT JOIN stream_screenshots
              ON stream_screenshots.result_key=channel_results.result_key
            WHERE channel_results.channel_key=?
            ORDER BY selected_rank IS NULL, selected_rank, valid DESC, speed DESC, delay ASC
            """,
            (channel_key,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["test_state"] = "tested" if item.get("tested_at") else "untested"
            item["headers"] = json.loads(item["headers"]) if item.get("headers") else None
            item["extra_data"] = json.loads(item["extra_data"]) if item.get("extra_data") else {}
            result.append(item)
        return result
    finally:
        return_db_connection(db_path, conn)


def get_stream_screenshot(db_path: str, result_key: str) -> dict[str, Any] | None:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM stream_screenshots WHERE result_key=?",
            (result_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        return_db_connection(db_path, conn)


def upsert_stream_screenshot(db_path: str, screenshot: dict) -> None:
    ensure_channel_repository(db_path)
    values = (
        screenshot.get("result_key"),
        screenshot.get("filename"),
        screenshot.get("status") or "failed",
        screenshot.get("captured_at"),
        screenshot.get("attempted_at") or time.time(),
        screenshot.get("width"),
        screenshot.get("height"),
        screenshot.get("error"),
    )
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO stream_screenshots(
                    result_key, filename, status, captured_at, attempted_at,
                    width, height, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(result_key) DO UPDATE SET
                    filename=excluded.filename,
                    status=excluded.status,
                    captured_at=excluded.captured_at,
                    attempted_at=excluded.attempted_at,
                    width=excluded.width,
                    height=excluded.height,
                    error=excluded.error
                """,
                values,
            )
            conn.commit()
        finally:
            return_db_connection(db_path, conn)


def prune_stream_screenshots(
    db_path: str,
    screenshot_dir: str = constants.screenshot_dir,
) -> dict[str, int]:
    ensure_channel_repository(db_path)
    os.makedirs(screenshot_dir, exist_ok=True)
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            active_keys = {
                row[0]
                for row in conn.execute("SELECT DISTINCT result_key FROM channel_results")
            }
            stored_rows = list(
                conn.execute("SELECT result_key, filename FROM stream_screenshots")
            )
            orphan_rows = [row for row in stored_rows if row[0] not in active_keys]
            if orphan_rows:
                conn.executemany(
                    "DELETE FROM stream_screenshots WHERE result_key=?",
                    [(row[0],) for row in orphan_rows],
                )
            conn.commit()
        finally:
            return_db_connection(db_path, conn)

    referenced = {
        filename
        for result_key, filename in stored_rows
        if result_key in active_keys and filename
    }
    removed = 0
    temporary = 0
    for filename in os.listdir(screenshot_dir):
        path = os.path.join(screenshot_dir, filename)
        if not os.path.isfile(path):
            continue
        is_temporary = filename.startswith(".")
        if is_temporary or filename not in referenced:
            try:
                os.unlink(path)
                removed += 1
                temporary += int(is_temporary)
            except OSError:
                pass
    return {
        "records": len(orphan_rows),
        "files": removed,
        "temporary_files": temporary,
    }


def list_streamable_results(db_path: str) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.channel_key, c.category, c.name AS channel_name,
                   r.result_key, r.url, r.selected_rank, r.speed, r.delay,
                   r.resolution, r.video_codec, r.audio_codec
            FROM channel_results r
            JOIN channels c ON c.channel_key=r.channel_key
            WHERE r.selected_rank IS NOT NULL
              AND r.url IS NOT NULL
              AND r.url != ''
            ORDER BY c.category, c.name, r.selected_rank
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def get_channel(db_path: str, channel_key: str) -> dict[str, Any] | None:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM channels WHERE channel_key=?", (channel_key,)).fetchone()
        return dict(row) if row else None
    finally:
        return_db_connection(db_path, conn)


def upsert_manual_channel(db_path: str, category: str, name: str) -> str:
    ensure_channel_repository(db_path)
    channel_key = stable_channel_id(category, name)
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO channels(
                channel_key, category, name, total_results, valid_results, selected_results,
                health, updated_at
            ) VALUES (?, ?, ?, 0, 0, 0, 'unknown', ?)
            ON CONFLICT(channel_key) DO UPDATE SET
                category=excluded.category, name=excluded.name, updated_at=excluded.updated_at
            """,
            (channel_key, category, name, time.time()),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)
    return channel_key


def delete_channel_records(db_path: str, channel_keys: list[str]) -> int:
    keys = [key for key in channel_keys if key]
    if not keys:
        return 0
    ensure_channel_repository(db_path)
    placeholders = ",".join("?" for _ in keys)
    conn = get_db_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        result_placeholders = ",".join("?" for _ in keys)
        result_keys = [
            row[0]
            for row in conn.execute(
                f"SELECT result_key FROM channel_results WHERE channel_key IN ({result_placeholders})",
                keys,
            )
        ]
        cursor = conn.execute(f"DELETE FROM channels WHERE channel_key IN ({placeholders})", keys)
        if result_keys:
            result_key_placeholders = ",".join("?" for _ in result_keys)
            remaining = {
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT result_key FROM channel_results WHERE result_key IN ({result_key_placeholders})",
                    result_keys,
                )
            }
            orphaned = [key for key in result_keys if key not in remaining]
            if orphaned:
                orphan_placeholders = ",".join("?" for _ in orphaned)
                conn.execute(
                    f"DELETE FROM stream_samples WHERE result_key IN ({orphan_placeholders})",
                    orphaned,
                )
                conn.execute(
                    f"DELETE FROM stream_screenshots WHERE result_key IN ({orphan_placeholders})",
                    orphaned,
                )
        conn.commit()
        return cursor.rowcount
    finally:
        return_db_connection(db_path, conn)


def delete_channel_results(
    db_path: str,
    channel_key: str,
    result_keys: list[str],
) -> list[str]:
    keys = list(dict.fromkeys(str(key) for key in result_keys if key))
    if not channel_key or not keys:
        return []
    ensure_channel_repository(db_path)
    placeholders = ",".join("?" for _ in keys)
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = [
                row[0]
                for row in conn.execute(
                    f"""
                    SELECT result_key
                    FROM channel_results
                    WHERE channel_key=? AND result_key IN ({placeholders})
                    """,
                    [channel_key, *keys],
                )
            ]
            if not existing:
                conn.commit()
                return []
            key_placeholders = ",".join("?" for _ in existing)
            conn.execute(
                f"""
                DELETE FROM channel_results
                WHERE channel_key=? AND result_key IN ({key_placeholders})
                """,
                [channel_key, *existing],
            )
            remaining = {
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT result_key FROM channel_results WHERE result_key IN ({key_placeholders})",
                    existing,
                )
            }
            orphaned = [key for key in existing if key not in remaining]
            if orphaned:
                orphan_placeholders = ",".join("?" for _ in orphaned)
                conn.execute(
                    f"DELETE FROM stream_samples WHERE result_key IN ({orphan_placeholders})",
                    orphaned,
                )
                conn.execute(
                    f"DELETE FROM stream_screenshots WHERE result_key IN ({orphan_placeholders})",
                    orphaned,
                )
            _refresh_channel_summary(conn, channel_key)
            conn.commit()
            return existing
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)


def add_manual_result(db_path: str, channel_key: str, url: str) -> str:
    ensure_channel_repository(db_path)
    result_key = stable_result_id(url, None)
    host = urlparse(url).hostname
    now = time.time()
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, host, origin, supply, valid,
                selected_rank, tested_at, last_seen_at, extra_data
            ) VALUES (?, ?, ?, ?, 'local', 0, 0, NULL, NULL, ?, '{}')
            ON CONFLICT(channel_key, result_key) DO UPDATE SET
                url=excluded.url, host=excluded.host, origin='local', last_seen_at=excluded.last_seen_at
            """,
            (channel_key, result_key, url, host, now),
        )
        conn.execute(
            """
            UPDATE channels SET
                total_results=(SELECT COUNT(*) FROM channel_results WHERE channel_key=?),
                updated_at=? WHERE channel_key=?
            """,
            (channel_key, now, channel_key),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)
    return result_key


def list_result_urls_by_channel(db_path: str) -> dict[str, list[str]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT channel_key, url FROM channel_results").fetchall()
        result: dict[str, list[str]] = {}
        for channel_key, url in rows:
            result.setdefault(channel_key, []).append(url)
        return result
    finally:
        return_db_connection(db_path, conn)


def set_channel_logo(db_path: str, channel_key: str, logo: str) -> None:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("UPDATE channels SET logo=?, updated_at=? WHERE channel_key=?", (logo.strip(), time.time(), channel_key))
        conn.commit()
    finally:
        return_db_connection(db_path, conn)


def update_result_measurement(db_path: str, channel_key: str, result_key: str, measurement: dict) -> None:
    ensure_channel_repository(db_path)
    test_status = measurement.get("test_status")
    failed_statuses = {"timeout", "request_error", "probe_error", "cancelled", "unreachable"}
    stale_measurement = test_status in failed_statuses or measurement.get("delay") in {-1, None}
    measurement_valid = _is_valid(measurement) and not stale_measurement
    values = (
        None if stale_measurement else measurement.get("speed"),
        None if stale_measurement else measurement.get("delay"),
        None if stale_measurement else measurement.get("resolution"),
        None if stale_measurement else measurement.get("fps"),
        None if stale_measurement else measurement.get("video_codec"),
        None if stale_measurement else measurement.get("audio_codec"),
        int(measurement_valid),
        test_status or ("valid" if measurement_valid else "invalid"),
        measurement.get("error_type"),
        time.time(),
        channel_key,
        result_key,
    )
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute(
                """
                UPDATE channel_results SET
                    speed=?, delay=?, resolution=?, fps=?, video_codec=?, audio_codec=?,
                    valid=?, test_status=?, error_type=?, tested_at=?
                    WHERE channel_key=? AND result_key=?
                """,
                values,
            )
            latest_run = conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if latest_run:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO candidate_measurements(
                        run_id, channel_key, result_key, speed, delay, resolution,
                        fps, video_codec, audio_codec, valid, test_status, error_type,
                        tested_at, measured_at
                    )
                    SELECT ?, channel_key, result_key, speed, delay, resolution,
                           fps, video_codec, audio_codec, valid, test_status, error_type,
                           tested_at, ?
                    FROM channel_results
                    WHERE channel_key=? AND result_key=?
                    """,
                    (latest_run[0], time.time(), channel_key, result_key),
                )
            conn.commit()
        finally:
            return_db_connection(db_path, conn)


def set_channel_selection(
    db_path: str,
    channel_key: str,
    selected: list[dict],
    mode: str = "manual",
) -> None:
    ensure_channel_repository(db_path)
    selected = selected[: config.output_urls_limit]
    selected_ranks = {
        stable_result_id(item.get("url", ""), item.get("headers")): rank
        for rank, item in enumerate(selected, start=1)
        if item.get("url")
    }
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE channel_results SET selected_rank=NULL WHERE channel_key=?", (channel_key,))
            conn.executemany(
                "UPDATE channel_results SET selected_rank=? WHERE channel_key=? AND result_key=?",
                [(rank, channel_key, result_key) for result_key, rank in selected_ranks.items()],
            )
            conn.execute("DELETE FROM channel_selection WHERE channel_key=?", (channel_key,))
            if mode == "manual":
                conn.executemany(
                    """
                    INSERT INTO channel_selection(
                        channel_key, result_key, selection_rank,
                        selection_state, pinned, updated_at
                    ) VALUES (?, ?, ?, 'included', 0, ?)
                    """,
                    [
                        (channel_key, result_key, rank, time.time())
                        for result_key, rank in selected_ranks.items()
                    ],
                )
            conn.execute(
                "UPDATE channels SET selection_mode=? WHERE channel_key=?",
                ("manual" if mode == "manual" else "auto", channel_key),
            )
            rows = conn.execute(
                "SELECT speed, delay, resolution, valid FROM channel_results WHERE channel_key=?",
                (channel_key,),
            ).fetchall()
            valid_rows = [row for row in rows if row[3]]
            speeds = [row[0] for row in valid_rows if isinstance(row[0], (int, float)) and not math.isinf(row[0])]
            delays = [row[1] for row in valid_rows if isinstance(row[1], (int, float)) and row[1] >= 0]
            resolutions = [row[2] for row in valid_rows if row[2]]
            health = "healthy" if len(valid_rows) >= 2 else "warning" if valid_rows else "offline"
            conn.execute(
                """
                UPDATE channels SET valid_results=?, selected_results=?, best_speed=?, min_delay=?,
                    max_resolution=?, health=?, updated_at=? WHERE channel_key=?
                """,
                (
                    len(valid_rows),
                    len(selected_ranks),
                    max(speeds, default=None),
                    min(delays, default=None),
                    max(resolutions, key=_resolution_value, default=None),
                    health,
                    time.time(),
                    channel_key,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            return_db_connection(db_path, conn)


def _auto_selection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order measured candidates using the configured automatic policy."""
    dimensions = list(config.sort_by)

    def number(value, default):
        try:
            value = float(value)
            return value if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    def key(row):
        # Whitelist/HLS entries are intentionally retained and should remain
        # ahead of ordinary probe results, matching the export sorter.
        retained = 0 if row.get("origin") in {"whitelist", "hls"} else 1
        values = []
        for dimension in dimensions:
            if dimension == "speed":
                values.append(-number(row.get("speed"), -math.inf))
            elif dimension == "delay":
                values.append(number(row.get("delay"), math.inf))
            else:
                values.append(-_resolution_value(row.get("resolution")))
        return retained, *values, str(row.get("result_key") or "")

    return sorted(
        [row for row in rows if _is_measured_valid(row)],
        key=key,
    )


def reset_channel_selection(db_path: str, channel_key: str) -> None:
    """Clear manual preferences and immediately recompute automatic output."""
    ensure_channel_repository(db_path)
    with _LOCK:
        conn = get_db_connection(db_path)
        try:
            conn.execute("DELETE FROM channel_selection WHERE channel_key=?", (channel_key,))
            conn.execute(
                "UPDATE channels SET selection_mode='auto', updated_at=? WHERE channel_key=?",
                (time.time(), channel_key),
            )
            conn.commit()
        finally:
            return_db_connection(db_path, conn)
    rows = list_channel_results(db_path, channel_key)
    set_channel_selection(db_path, channel_key, _auto_selection_rows(rows), mode="auto")


def auto_select_channel(db_path: str, channel_key: str) -> list[dict[str, Any]]:
    """Recompute and persist automatic output selection for one channel."""
    rows = list_channel_results(db_path, channel_key)
    selected = _auto_selection_rows(rows)[: config.output_urls_limit]
    set_channel_selection(db_path, channel_key, selected, mode="auto")
    return selected


def _load_manual_selections(db_path: str) -> dict[str, list[str]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT c.channel_key, s.result_key
            FROM channels c
            LEFT JOIN channel_selection s ON s.channel_key=c.channel_key
            WHERE c.selection_mode='manual'
            ORDER BY c.channel_key, s.selection_rank IS NULL, s.selection_rank
            """
        ).fetchall()
        result: dict[str, list[str]] = {}
        for channel_key, result_key in rows:
            result.setdefault(channel_key, [])
            if result_key:
                result[channel_key].append(result_key)
        return result
    finally:
        return_db_connection(db_path, conn)


def load_selected_snapshot(db_path: str) -> dict:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = {}
        channels = conn.execute("SELECT category, name FROM channels ORDER BY category, name").fetchall()
        for channel in channels:
            result.setdefault(channel["category"], {}).setdefault(channel["name"], [])
        rows = conn.execute(
            """
            SELECT c.category, c.name, r.* FROM channel_results r
            JOIN channels c ON c.channel_key=r.channel_key
            WHERE r.selected_rank IS NOT NULL
            ORDER BY c.category, c.name, r.selected_rank
            """
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            row["id"] = row["result_key"]
            row["headers"] = json.loads(row["headers"]) if row.get("headers") else None
            row.update(json.loads(row["extra_data"]) if row.get("extra_data") else {})
            result.setdefault(row["category"], {}).setdefault(row["name"], []).append(row)
        return result
    finally:
        return_db_connection(db_path, conn)


def list_output_snapshot(db_path: str, run_id: str) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT s.*, c.category, c.name
            FROM output_snapshots s
            JOIN channels c ON c.channel_key=s.channel_key
            WHERE s.run_id=?
            ORDER BY c.category, c.name, s.output_rank
            """,
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def list_candidate_history(
    db_path: str,
    channel_key: str | None = None,
    result_key: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return discovered candidates recorded for completed collection runs."""
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params: list[Any] = []
        if channel_key:
            clauses.append("h.channel_key=?")
            params.append(channel_key)
        if result_key:
            clauses.append("h.result_key=?")
            params.append(result_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = conn.execute(
            f"""
            SELECT h.*, r.started_at, r.finished_at, r.status AS run_status
            FROM candidate_history h
            LEFT JOIN runs r ON r.run_id=h.run_id
            {where}
            ORDER BY h.last_seen_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def list_candidate_pool(
    db_path: str,
    channel_key: str | None = None,
    include_stale: bool = True,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """List the stable, deduplicated candidate pool across collection runs."""
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params: list[Any] = []
        if channel_key:
            clauses.append("p.channel_key=?")
            params.append(channel_key)
        if not include_stale:
            clauses.append(
                "p.last_seen_at >= COALESCE((SELECT started_at FROM runs WHERE run_id=p.last_run_id), 0)"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = conn.execute(
            f"SELECT p.* FROM candidate_pool p {where} ORDER BY p.last_seen_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def list_candidate_measurements(
    db_path: str,
    channel_key: str | None = None,
    result_key: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return per-run measurement snapshots for candidate quality history."""
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params: list[Any] = []
        if channel_key:
            clauses.append("m.channel_key=?")
            params.append(channel_key)
        if result_key:
            clauses.append("m.result_key=?")
            params.append(result_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        rows = conn.execute(
            f"""
            SELECT m.*, r.started_at, r.finished_at, r.status AS run_status
            FROM candidate_measurements m
            LEFT JOIN runs r ON r.run_id=m.run_id
            {where}
            ORDER BY m.measured_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def begin_operation(db_path: str, operation: str, target_type: str, target_key: str | None) -> str:
    ensure_channel_repository(db_path)
    operation_id = uuid.uuid4().hex
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO operation_history(operation_id, operation, target_type, target_key, started_at, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (operation_id, operation, target_type, target_key, time.time()),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)
    return operation_id


def finish_operation(db_path: str, operation_id: str, status: str, message: str = "") -> None:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            "UPDATE operation_history SET finished_at=?, status=?, message=? WHERE operation_id=?",
            (time.time(), status, message, operation_id),
        )
        conn.commit()
    finally:
        return_db_connection(db_path, conn)


def list_operations(db_path: str, limit: int = 200) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM operation_history ORDER BY started_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)


def append_stream_samples(db_path: str, sampled_at: float, streams: list[dict]) -> None:
    if not streams:
        return
    ensure_channel_repository(db_path)
    rows = [(
        sampled_at,
        str(stream.get("result_key") or stream.get("name") or ""),
        int(stream.get("clients") or 0),
        float(stream.get("bw_in") or 0),
        float(stream.get("bw_out") or 0),
        int(stream.get("bytes_in") or 0),
        int(stream.get("bytes_out") or 0),
        int(bool(stream.get("active"))),
    ) for stream in streams if stream.get("result_key") or stream.get("name")]
    conn = get_db_connection(db_path)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO stream_samples(
                sampled_at, result_key, clients, bw_in, bw_out, bytes_in, bytes_out, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute("DELETE FROM stream_samples WHERE sampled_at < ?", (sampled_at - 7 * 86400,))
        conn.commit()
    finally:
        return_db_connection(db_path, conn)


def result_metadata_map(db_path: str, result_keys: list[str]) -> dict[str, dict[str, Any]]:
    keys = [str(key) for key in result_keys if key]
    if not keys:
        return {}
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"""
            SELECT r.result_key, r.url, r.selected_rank, c.channel_key, c.category, c.name
            FROM channel_results r JOIN channels c ON c.channel_key=r.channel_key
            WHERE r.result_key IN ({placeholders})
            """,
            keys,
        ).fetchall()
        return {row["result_key"]: dict(row) for row in rows}
    finally:
        return_db_connection(db_path, conn)


def list_runs(db_path: str, limit: int = 100) -> list[dict[str, Any]]:
    ensure_channel_repository(db_path)
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        return_db_connection(db_path, conn)

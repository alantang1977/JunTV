from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


CATEGORY_DEFINITIONS = [
    {
        "code": "news",
        "color": "#2563EB",
        "zh_CN": ("新闻资讯", ["城市新闻", "环球资讯", "财经观察", "天气生活"]),
        "en": ("News", ["City News", "World Update", "Market Watch", "Weather & Life"]),
    },
    {
        "code": "sports",
        "color": "#059669",
        "zh_CN": ("体育赛事", ["体育一台", "赛事精选", "户外运动", "极速赛场"]),
        "en": ("Sports", ["Sports One", "Match Select", "Outdoor Sports", "Speed Arena"]),
    },
    {
        "code": "movies",
        "color": "#7C3AED",
        "zh_CN": ("影视娱乐", ["电影精选", "剧集时光", "经典影院", "音乐现场"]),
        "en": ("Entertainment", ["Movie Select", "Series Time", "Classic Cinema", "Live Music"]),
    },
    {
        "code": "learning",
        "color": "#EA580C",
        "zh_CN": ("少儿科教", ["少儿乐园", "自然探索", "科学课堂", "历史人文"]),
        "en": ("Kids & Learning", ["Kids World", "Nature Explorer", "Science Class", "History & Culture"]),
    },
]

EXPECTED_CHANNELS = 16
EXPECTED_RESULTS = 48
EXPECTED_VALID_RESULTS = 40
EXPECTED_SELECTED_RESULTS = 32
EXPECTED_ACTIVE_STREAMS = 2
EXPECTED_STARTING_STREAMS = 1


def normalized_language(language: str) -> str:
    return "en" if str(language).lower().startswith("en") else "zh_CN"


def build_demo_data(language: str, logo_dir: Path) -> dict:
    locale = normalized_language(language)
    base_data: dict[str, dict[str, list[dict]]] = {}
    tested_data: dict[str, dict[str, list[dict]]] = {}
    selected_data: dict[str, dict[str, list[dict]]] = {}
    logo_specs = []
    channel_logos = {}
    channel_index = 0

    for category in CATEGORY_DEFINITIONS:
        category_name, channel_names = category[locale]
        base_data[category_name] = {}
        tested_data[category_name] = {}
        selected_data[category_name] = {}
        for position, channel_name in enumerate(channel_names, start=1):
            channel_index += 1
            code = f"{category['code']}-{position}"
            logo_path = logo_dir / f"{code}.png"
            logo_specs.append({
                "path": logo_path,
                "label": f"{category['code'][0].upper()}{position}",
                "color": category["color"],
            })
            channel_logos[(category_name, channel_name)] = str(logo_path)
            base_items = []
            tested_items = []
            for result_index in range(3):
                url = f"https://demo.invalid/live/{code}/source-{result_index + 1}.m3u8"
                base_item = {
                    "url": url,
                    "host": f"edge-{result_index + 1}.demo.invalid",
                    "origin": "demo",
                    "ipv_type": "IPv6" if result_index == 2 and channel_index % 3 == 0 else "IPv4",
                    "location": "演示节点" if locale == "zh_CN" else "Demo edge",
                    "isp": "DemoNet",
                    "tvg_logo": str(logo_path),
                }
                valid = result_index < 2 or channel_index % 2 == 0
                resolution = (
                    "3840x2160"
                    if channel_index % 5 == 0 and result_index == 0
                    else "1920x1080"
                    if result_index < 2
                    else "1280x720"
                )
                measurement = {
                    **base_item,
                    "speed": round(1.85 + channel_index * 0.17 - result_index * 0.38, 2) if valid else 0,
                    "delay": 32 + channel_index * 3 + result_index * 19 if valid else -1,
                    "resolution": resolution if valid else "",
                    "fps": 50 if channel_index % 4 == 0 and valid else 25 if valid else 0,
                    "video_codec": "hevc" if resolution == "3840x2160" and valid else "h264" if valid else "",
                    "audio_codec": "aac" if valid else "",
                }
                base_items.append(base_item)
                tested_items.append(measurement)
            base_data[category_name][channel_name] = base_items
            tested_data[category_name][channel_name] = tested_items
            selected_data[category_name][channel_name] = tested_items[:2]

    return {
        "language": locale,
        "base_data": base_data,
        "tested_data": tested_data,
        "selected_data": selected_data,
        "logo_specs": logo_specs,
        "channel_logos": channel_logos,
    }


def generate_demo_logos(logo_specs: list[dict]) -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen

    for spec in logo_specs:
        path = Path(spec["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(128, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 128, 80)
        base = QColor(spec["color"])
        gradient.setColorAt(0, base.lighter(118))
        gradient.setColorAt(1, base.darker(112))
        painter.setBrush(gradient)
        painter.setPen(QPen(base.darker(130), 2))
        painter.drawRoundedRect(QRectF(2, 2, 124, 76), 18, 18)
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Helvetica Neue")
        font.setBold(True)
        font.setPixelSize(34)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 128, 80), Qt.AlignmentFlag.AlignCenter, spec["label"])
        painter.end()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Unable to write demo logo: {path}")


def seed_demo_repository(db_path: Path, demo_data: dict, now: float | None = None) -> dict:
    from utils.channel_repository import ensure_channel_repository, sync_channel_snapshot

    timestamp = float(now or time.time())
    sync_channel_snapshot(
        str(db_path),
        demo_data["base_data"],
        demo_data["tested_data"],
        demo_data["selected_data"],
    )
    ensure_channel_repository(str(db_path))
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        channels = connection.execute(
            "SELECT channel_key, category, name FROM channels ORDER BY category, name"
        ).fetchall()
        for index, channel in enumerate(channels):
            updated_at = timestamp - 90 - index * 67
            connection.execute(
                "UPDATE channels SET logo=?, updated_at=? WHERE channel_key=?",
                (
                    demo_data["channel_logos"][(channel["category"], channel["name"])],
                    updated_at,
                    channel["channel_key"],
                ),
            )
            connection.execute(
                "UPDATE channel_results SET tested_at=?, last_seen_at=? WHERE channel_key=?",
                (updated_at, updated_at, channel["channel_key"]),
            )

        connection.execute("DELETE FROM runs")
        connection.execute("DELETE FROM operation_history")
        connection.execute(
            """
            INSERT INTO runs(run_id, started_at, finished_at, status, config_hash, error)
            VALUES (?, ?, ?, 'success', 'gui-showcase', NULL)
            """,
            ("showcase-full-update", timestamp - 640, timestamp - 492),
        )
        operation_rows = [
            ("showcase-retest-channel", "retest_channel", "channel", channels[2]["channel_key"], 390, 354),
            ("showcase-retest-result", "retest_result", "result", None, 286, 268),
            ("showcase-stream-start", "start_stream", "channel", channels[6]["channel_key"], 172, 168),
        ]
        for operation_id, operation, target_type, target_key, started_ago, finished_ago in operation_rows:
            if target_type == "result":
                row = connection.execute(
                    """
                    SELECT result_key FROM channel_results
                    WHERE selected_rank=1 ORDER BY channel_key LIMIT 1 OFFSET 4
                    """
                ).fetchone()
                target_key = row["result_key"]
            connection.execute(
                """
                INSERT INTO operation_history(
                    operation_id, operation, target_type, target_key,
                    started_at, finished_at, status, message
                ) VALUES (?, ?, ?, ?, ?, ?, 'success', '')
                """,
                (
                    operation_id,
                    operation,
                    target_type,
                    target_key,
                    timestamp - started_ago,
                    timestamp - finished_ago,
                ),
            )
        connection.commit()
        snapshot = _build_stream_snapshot(connection, timestamp)
    finally:
        connection.close()
    validate_demo_repository(db_path, snapshot)
    return snapshot


def _build_stream_snapshot(connection: sqlite3.Connection, timestamp: float) -> dict:
    channel_rows = connection.execute(
        "SELECT channel_key, category, name FROM channels ORDER BY category, name"
    ).fetchall()
    selected_channels = [channel_rows[index] for index in (1, 5, 9)]
    stream_rows = []
    for index, channel in enumerate(selected_channels):
        result = connection.execute(
            """
            SELECT result_key, url, resolution, video_codec, audio_codec
            FROM channel_results
            WHERE channel_key=? AND selected_rank=1
            """,
            (channel["channel_key"],),
        ).fetchone()
        starting = index == 2
        clients = (3, 2, 0)[index]
        bandwidth = (8_400_000, 5_600_000, 0)[index]
        stream_rows.append({
            "result_key": result["result_key"],
            "name": result["result_key"],
            "channel_key": channel["channel_key"],
            "category": channel["category"],
            "channel_name": channel["name"],
            "url": result["url"],
            "state": "starting" if starting else "active",
            "active": not starting,
            "clients": clients,
            "bw_in": 4_200_000 if not starting else 0,
            "bw_out": bandwidth,
            "bytes_in": 182_400_000 if not starting else 0,
            "bytes_out": 364_800_000 if not starting else 0,
            "video_codec": result["video_codec"] if not starting else "",
            "audio_codec": result["audio_codec"] if not starting else "",
            "resolution": result["resolution"] if not starting else "",
            "fps": 25 if not starting else 0,
            "uptime": ("00:18:42", "00:07:16", "--")[index],
            "idle_remaining": (None, 214, None)[index],
            "client_details": [
                {
                    "id": f"demo-client-{index}-{client}",
                    "state": "playing",
                    "address": f"192.168.*.*:{5200 + client}",
                    "dropped": 0,
                    "timestamp": "",
                    "av_sync": 0,
                    "uptime": "00:03:24",
                }
                for client in range(clients)
            ],
        })
    active_keys = [row["result_key"] for row in stream_rows if row["state"] == "active"]
    starting_keys = [row["result_key"] for row in stream_rows if row["state"] == "starting"]
    return {
        "available": True,
        "sampled_at": timestamp,
        "uptime": "03:42:18",
        "accepted": 18,
        "bw_in": sum(row["bw_in"] for row in stream_rows),
        "bw_out": sum(row["bw_out"] for row in stream_rows),
        "bytes_in": sum(row["bytes_in"] for row in stream_rows),
        "bytes_out": sum(row["bytes_out"] for row in stream_rows),
        "streams": stream_rows,
        "max_streams": 6,
        "active_count": len(active_keys),
        "starting_count": len(starting_keys),
        "available_slots": 3,
        "active_streams": active_keys,
        "starting_streams": starting_keys,
    }


def validate_demo_repository(db_path: Path, snapshot: dict) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        counts = {
            "channels": connection.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
            "results": connection.execute("SELECT COUNT(*) FROM channel_results").fetchone()[0],
            "valid_results": connection.execute(
                "SELECT COUNT(*) FROM channel_results WHERE valid=1"
            ).fetchone()[0],
            "selected_results": connection.execute(
                "SELECT COUNT(*) FROM channel_results WHERE selected_rank IS NOT NULL"
            ).fetchone()[0],
        }
        known_results = {
            row[0] for row in connection.execute("SELECT result_key FROM channel_results")
        }
    finally:
        connection.close()
    expected = {
        "channels": EXPECTED_CHANNELS,
        "results": EXPECTED_RESULTS,
        "valid_results": EXPECTED_VALID_RESULTS,
        "selected_results": EXPECTED_SELECTED_RESULTS,
    }
    if counts != expected:
        raise ValueError(f"Unexpected GUI showcase counts: {counts}, expected {expected}")
    stream_keys = {row["result_key"] for row in snapshot.get("streams", [])}
    if not stream_keys.issubset(known_results):
        raise ValueError("GUI showcase stream snapshot references unknown results")
    if snapshot.get("active_count") != EXPECTED_ACTIVE_STREAMS:
        raise ValueError("GUI showcase active stream count is invalid")
    if snapshot.get("starting_count") != EXPECTED_STARTING_STREAMS:
        raise ValueError("GUI showcase starting stream count is invalid")
    return {
        **counts,
        "active_streams": snapshot["active_count"],
        "starting_streams": snapshot["starting_count"],
    }


def summary_json(db_path: Path, snapshot: dict) -> str:
    return json.dumps(
        validate_demo_repository(db_path, snapshot),
        ensure_ascii=False,
        sort_keys=True,
    )

import asyncio
from collections import defaultdict

import utils.constants as constants
from utils.channel import sort_channel_result, write_channel_to_file
from utils.channel_repository import (
    begin_operation,
    finish_operation,
    get_channel,
    list_channel_results,
    list_channels,
    load_selected_snapshot,
    set_channel_selection,
    upsert_stream_screenshot,
    update_result_measurement,
)
from utils.config import config
from utils.i18n import t
from utils.ffmpeg import capture_stream_screenshot
from utils.requests.tools import headers as request_headers
from utils.speed import get_speed, invalidate_speed_cache


def _as_channel_item(row: dict) -> dict:
    extra = row.get("extra_data") or {}
    return {
        "id": row["result_key"],
        "url": row["url"],
        "host": row.get("host"),
        "headers": row.get("headers"),
        "origin": row.get("origin"),
        "ipv_type": row.get("ipv_type"),
        "location": row.get("location"),
        "isp": row.get("isp"),
        "speed": row.get("speed"),
        "delay": row.get("delay"),
        "resolution": row.get("resolution"),
        "fps": row.get("fps"),
        "video_codec": row.get("video_codec"),
        "audio_codec": row.get("audio_codec"),
        "supply": bool(row.get("supply")),
        "date": extra.get("date"),
        "catchup": extra.get("catchup"),
        "tvg_logo": extra.get("tvg_logo"),
        "extra_info": extra.get("extra_info") or "",
    }


class ChannelOperations:
    def __init__(self, db_path: str = constants.channel_results_path):
        self.db_path = db_path

    async def retest_result(self, channel_key: str, result_key: str, progress=None) -> dict:
        operation_id = begin_operation(self.db_path, "retest_result", "result", result_key)
        try:
            channel = get_channel(self.db_path, channel_key)
            rows = list_channel_results(self.db_path, channel_key)
            row = next((item for item in rows if item["result_key"] == result_key), None)
            if not channel or not row:
                raise ValueError(t("msg.channel_result_not_found"))
            item = _as_channel_item(row)
            invalidate_speed_cache(item)
            if progress:
                progress(0, 1, channel["name"])
            result = await get_speed(
                item,
                headers=item.get("headers"),
                filter_resolution=config.open_filter_resolution,
                timeout=config.speed_test_timeout,
            )
            merged = {**item, **result}
            update_result_measurement(self.db_path, channel_key, result_key, merged)
            if progress:
                progress(1, 1, channel["name"])
            self._resort_and_publish(channel_key)
            finish_operation(self.db_path, operation_id, "success")
            return merged
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    async def retest_results(
        self,
        channel_key: str,
        result_keys: list[str],
        progress=None,
    ) -> list[dict]:
        """Retest a user-selected subset of a channel's candidate pool."""
        operation_id = begin_operation(self.db_path, "retest_results", "channel", channel_key)
        try:
            channel = get_channel(self.db_path, channel_key)
            rows = list_channel_results(self.db_path, channel_key)
            requested = list(dict.fromkeys(key for key in result_keys if key))
            row_map = {row["result_key"]: row for row in rows}
            targets = [row_map[key] for key in requested if key in row_map]
            if not channel or len(targets) != len(requested):
                raise ValueError(t("msg.channel_result_not_found"))
            total = len(targets)
            concurrency = max(1, min(config.performance_settings.speed_test_concurrency, total or 1))
            semaphore = asyncio.Semaphore(concurrency)
            completed = 0

            async def test_one(row):
                nonlocal completed
                item = _as_channel_item(row)
                invalidate_speed_cache(item)
                async with semaphore:
                    measured = await get_speed(
                        item,
                        headers=item.get("headers"),
                        filter_resolution=config.open_filter_resolution,
                        timeout=config.speed_test_timeout,
                    )
                merged = {**item, **measured}
                update_result_measurement(self.db_path, channel_key, row["result_key"], merged)
                completed += 1
                if progress:
                    progress(completed, total, channel["name"])
                return merged

            results = await asyncio.gather(*(test_one(row) for row in targets))
            self._resort_and_publish(channel_key)
            finish_operation(self.db_path, operation_id, "success")
            return results
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    async def capture_result_screenshot(
        self,
        channel_key: str,
        result_key: str,
        progress=None,
    ) -> dict:
        operation_id = begin_operation(
            self.db_path,
            "capture_result_screenshot",
            "result",
            result_key,
        )
        try:
            channel = get_channel(self.db_path, channel_key)
            rows = list_channel_results(self.db_path, channel_key)
            row = next((item for item in rows if item["result_key"] == result_key), None)
            if not channel or not row:
                raise ValueError(t("msg.channel_result_not_found"))
            if progress:
                progress(0, 1, channel["name"])
            screenshot = await capture_stream_screenshot(
                row["url"].partition("$")[0],
                result_key,
                constants.screenshot_dir,
                headers={
                    **request_headers,
                    **(row.get("headers") or {}),
                },
                timeout=config.stream_screenshot_timeout,
                width=config.stream_screenshot_width,
            )
            upsert_stream_screenshot(self.db_path, screenshot)
            if screenshot.get("status") != "success":
                raise RuntimeError(
                    t(
                        f"desktop.screenshot_error_{screenshot.get('error')}",
                        screenshot.get("error") or t("desktop.screenshot_failed"),
                    )
                )
            merged = {
                **_as_channel_item(row),
                **{
                    key: screenshot[key]
                    for key in ("resolution", "fps", "video_codec", "audio_codec")
                    if screenshot.get(key) is not None
                },
            }
            update_result_measurement(self.db_path, channel_key, result_key, merged)
            self._resort_and_publish(channel_key)
            if progress:
                progress(1, 1, channel["name"])
            finish_operation(self.db_path, operation_id, "success")
            return {
                **screenshot,
                "channel_key": channel_key,
                "channel_name": channel["name"],
            }
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    async def capture_result_screenshots(
        self,
        channel_key: str,
        result_keys: list[str],
        progress=None,
    ) -> dict:
        operation_id = begin_operation(
            self.db_path,
            "capture_result_screenshots",
            "channel",
            channel_key,
        )
        try:
            channel = get_channel(self.db_path, channel_key)
            rows = list_channel_results(self.db_path, channel_key)
            requested_keys = list(dict.fromkeys(key for key in result_keys if key))
            if not requested_keys:
                raise ValueError(t("msg.channel_result_not_found"))
            row_map = {row["result_key"]: row for row in rows}
            targets = [row_map[key] for key in requested_keys if key in row_map]
            if not channel:
                raise ValueError(t("msg.channel_not_found"))
            if len(targets) != len(requested_keys):
                raise ValueError(t("msg.channel_result_not_found"))

            total = len(targets)
            completed = 0
            semaphore = asyncio.Semaphore(
                max(1, min(2, config.performance_settings.probe_concurrency, total or 1))
            )

            async def capture_one(row: dict):
                nonlocal completed
                async with semaphore:
                    screenshot = await capture_stream_screenshot(
                        row["url"].partition("$")[0],
                        row["result_key"],
                        constants.screenshot_dir,
                        headers={
                            **request_headers,
                            **(row.get("headers") or {}),
                        },
                        timeout=config.stream_screenshot_timeout,
                        width=config.stream_screenshot_width,
                    )
                await asyncio.to_thread(
                    upsert_stream_screenshot,
                    self.db_path,
                    screenshot,
                )
                if screenshot.get("status") == "success":
                    merged = {
                        **_as_channel_item(row),
                        **{
                            key: screenshot[key]
                            for key in ("resolution", "fps", "video_codec", "audio_codec")
                            if screenshot.get(key) is not None
                        },
                    }
                    await asyncio.to_thread(
                        update_result_measurement,
                        self.db_path,
                        channel_key,
                        row["result_key"],
                        merged,
                    )
                completed += 1
                if progress:
                    progress(completed, total, channel["name"])
                return screenshot

            screenshots = await asyncio.gather(
                *(capture_one(row) for row in targets)
            ) if targets else []
            if any(item.get("status") == "success" for item in screenshots):
                self._resort_and_publish(channel_key)
            failed = sum(item.get("status") != "success" for item in screenshots)
            finish_operation(
                self.db_path,
                operation_id,
                "partial" if failed else "success",
                t("desktop.screenshot_batch_result").format(
                    success=len(screenshots) - failed,
                    failed=failed,
                ),
            )
            return {
                "channel_key": channel_key,
                "results": screenshots,
                "success": len(screenshots) - failed,
                "failed": failed,
            }
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    async def retest_channel(self, channel_key: str, progress=None) -> list[dict]:
        operation_id = begin_operation(self.db_path, "retest_channel", "channel", channel_key)
        try:
            channel = get_channel(self.db_path, channel_key)
            rows = list_channel_results(self.db_path, channel_key)
            if not channel:
                raise ValueError(t("msg.channel_not_found"))
            results = []
            total = len(rows)
            concurrency = max(1, min(config.performance_settings.speed_test_concurrency, total or 1))
            semaphore = asyncio.Semaphore(concurrency)

            async def test_one(index: int, row: dict):
                item = _as_channel_item(row)
                invalidate_speed_cache(item)
                async with semaphore:
                    measured = await get_speed(
                        item,
                        headers=item.get("headers"),
                        filter_resolution=config.open_filter_resolution,
                        timeout=config.speed_test_timeout,
                    )
                merged = {**item, **measured}
                update_result_measurement(self.db_path, channel_key, row["result_key"], merged)
                if progress:
                    progress(index + 1, total, channel["name"])
                return merged

            tasks = [asyncio.create_task(test_one(index, row)) for index, row in enumerate(rows)]
            if tasks:
                results = await asyncio.gather(*tasks)
            self._resort_and_publish(channel_key)
            finish_operation(self.db_path, operation_id, "success")
            return results
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    async def retest_category(self, category: str, progress=None) -> list[dict]:
        operation_id = begin_operation(self.db_path, "retest_category", "category", category)
        try:
            channels = list_channels(self.db_path, category=category)
            results = []
            total = len(channels)
            for index, channel in enumerate(channels):
                results.extend(await self.retest_channel(channel["channel_key"]))
                if progress:
                    progress(index + 1, total, channel["name"])
            finish_operation(self.db_path, operation_id, "success")
            return results
        except asyncio.CancelledError:
            finish_operation(self.db_path, operation_id, "cancelled")
            raise
        except Exception as exc:
            finish_operation(self.db_path, operation_id, "failed", str(exc))
            raise

    def _resort_and_publish(self, channel_key: str):
        channel = get_channel(self.db_path, channel_key)
        rows = list_channel_results(self.db_path, channel_key)
        if not channel:
            return
        items = [_as_channel_item(row) for row in rows]
        if channel.get("selection_mode") == "manual":
            selected = [row for row in rows if row.get("selected_rank") is not None]
            selected.sort(key=lambda row: row.get("selected_rank") or 0)
            set_channel_selection(self.db_path, channel_key, selected, mode="manual")
            snapshot = load_selected_snapshot(self.db_path)
            write_channel_to_file(snapshot, ipv6=config.ipv6_support, skip_print=True, is_last=True)
            return
        base = defaultdict(lambda: defaultdict(list))
        tested = defaultdict(lambda: defaultdict(list))
        base[channel["category"]][channel["name"]] = items
        tested[channel["category"]][channel["name"]] = items
        sorted_data = sort_channel_result(
            base,
            result=tested,
            filter_host=False,
            ipv6_support=True,
            cate=channel["category"],
            name=channel["name"],
        )
        selected = sorted_data.get(channel["category"], {}).get(channel["name"], [])
        set_channel_selection(self.db_path, channel_key, selected, mode="auto")
        snapshot = load_selected_snapshot(self.db_path)
        write_channel_to_file(snapshot, ipv6=config.ipv6_support, skip_print=True, is_last=True)
